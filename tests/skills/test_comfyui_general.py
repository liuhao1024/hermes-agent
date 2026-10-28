"""Offline regressions for Comfy dependency readiness and schema summaries.

Run with: python -B tests/skills/test_comfyui_general.py -v
No server, accelerator, credentials or third-party test dependencies needed.
"""
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / 'optional-skills/creative/comfyui/scripts'
sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    spec = spec_from_file_location('general_' + name, SCRIPTS / (name + '.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == (SCRIPTS / (name + '.py')).resolve()
    return module


CHECK = load_script('check_deps')
SCHEMA = load_script('extract_schema')
HARDWARE = load_script('hardware_check')


class Response:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def json(self):
        return self.body

    def text(self):
        return json.dumps(self.body)


def workflow(text='normal prompt', checkpoint=True):
    graph = {'6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': text}}}
    if checkpoint:
        graph['4'] = {'class_type': 'CheckpointLoaderSimple',
                      'inputs': {'ckpt_name': 'example.safetensors'}}
    return graph


class Readiness(unittest.TestCase):
    def run_check(self, graph, models=(200, ['example.safetensors']),
                  embeddings=(200, []), nodes=(200, None), cli=False):
        requested = []

        def http_get(url, **kwargs):
            path = urlparse(url).path
            requested.append(path)
            if path == '/object_info':
                status, body = nodes
                return Response(status, body if body is not None else {
                    node['class_type']: {} for node in graph.values()})
            if path == '/models/checkpoints':
                return Response(*models)
            if path == '/embeddings':
                return Response(*embeddings)
            raise AssertionError('Unexpected endpoint: ' + path)

        with patch.object(CHECK, 'http_get', side_effect=http_get):
            if cli:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'workflow.json'
                    path.write_text(json.dumps(graph), encoding='utf-8')
                    output = io.StringIO()
                    with redirect_stdout(output):
                        code = CHECK.main([str(path), '--host', 'http://fixture.invalid'])
                    report = json.loads(output.getvalue())
                    self.assertEqual(code, 0 if report['is_ready'] else 1)
            else:
                report = CHECK.check_deps(graph, 'http://fixture.invalid')
        self.assertIn('/object_info', requested)
        return report

    def test_unknown_required_model_forbidden_cli(self):
        report = self.run_check(workflow(), models=(403, {}), cli=True)
        self.assertFalse(report['is_ready'])
        self.assertEqual(report['missing_models'], [])
        self.assertEqual(report['folder_errors']['checkpoints']['http_status'], 403)

    def test_unknown_required_model_endpoint_missing(self):
        report = self.run_check(workflow(), models=(404, {}))
        self.assertFalse(report['is_ready'])
        self.assertEqual(report['folder_errors']['checkpoints']['http_status'], 404)

    def test_unknown_required_embedding(self):
        report = self.run_check(workflow('embedding:missing'), embeddings=(403, {}))
        self.assertFalse(report['is_ready'])
        self.assertEqual(report['missing_embeddings'], [])
        self.assertIn('embeddings', report['folder_errors'])

    def test_no_embedding_endpoint_irrelevant_cli(self):
        report = self.run_check(workflow(), embeddings=(403, {}), cli=True)
        self.assertTrue(report['is_ready'])
        self.assertEqual(report['folder_errors'], {})

    def test_no_model_or_embedding_refs(self):
        report = self.run_check(workflow(checkpoint=False), embeddings=(404, {}))
        self.assertTrue(report['is_ready'])
        self.assertEqual(report['folder_errors'], {})

    def test_known_empty_models_not_ready(self):
        report = self.run_check(workflow(), models=(200, []))
        self.assertFalse(report['is_ready'])
        self.assertEqual(len(report['missing_models']), 1)
        self.assertEqual(report['folder_errors'], {})

    def test_known_dependencies_ready(self):
        report = self.run_check(workflow('embedding:present'), embeddings=(200, ['present.pt']))
        self.assertTrue(report['is_ready'])
        self.assertEqual(report['missing_embeddings'], [])

    def test_missing_embedding_not_ready(self):
        report = self.run_check(workflow('embedding:missing'))
        self.assertFalse(report['is_ready'])
        self.assertEqual(len(report['missing_embeddings']), 1)

    def test_unqueried_nodes_not_ready(self):
        report = self.run_check(workflow(), nodes=(403, {}))
        self.assertFalse(report['is_ready'])
        self.assertTrue(report['node_check_skipped'])


class SchemaSummary(unittest.TestCase):
    def graph(self, negatives):
        graph = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'positive'}}}
        for n in range(negatives):
            graph[str(n + 2)] = {'class_type': 'CLIPTextEncode',
                                 '_meta': {'title': 'Negative Prompt'},
                                 'inputs': {'text': 'negative'}}
        return graph

    def test_duplicate_negative_parameters(self):
        report = SCHEMA.extract_schema(self.graph(2))
        self.assertNotIn('negative_prompt', report['parameters'])
        self.assertIn('negative_prompt_2', report['parameters'])
        self.assertIn('negative_prompt_3', report['parameters'])
        self.assertTrue(report['summary']['has_negative_prompt'])

    def test_single_negative_parameter(self):
        report = SCHEMA.extract_schema(self.graph(1))
        self.assertIn('negative_prompt', report['parameters'])
        self.assertTrue(report['summary']['has_negative_prompt'])

    def test_positive_only_not_negative(self):
        self.assertFalse(SCHEMA.extract_schema(self.graph(0))['summary']['has_negative_prompt'])

    def test_summary_only_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'workflow.json'
            path.write_text(json.dumps(self.graph(2)), encoding='utf-8')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(SCHEMA.main([str(path), '--summary-only']), 0)
            self.assertTrue(json.loads(output.getvalue())['has_negative_prompt'])


class CapacityNotes(unittest.TestCase):
    def test_high_vram_is_heuristic_not_universal_promise(self):
        for vendor in ['nvidia', 'amd']:
            for vram in [12, 24]:
                with self.subTest(vendor=vendor, vram=vram):
                    verdict, route, notes = HARDWARE.classify(
                        {'vendor': vendor, 'name': 'Synthetic GPU', 'vram_gb': vram},
                        64, 100, wsl=False, rosetta=False)
                    self.assertEqual((verdict, route), ('ok', vendor))
                    text = ' '.join(notes).lower()
                    self.assertNotIn('can run everything', text)
                    self.assertIn('hardware heuristic', text)
                    self.assertIn('precision', text)
                    self.assertIn('dimensions', text)
                    self.assertIn('sequence length', text)
                    self.assertIn('concurrency', text)

    def test_existing_marginal_route_unchanged(self):
        verdict, route, _ = HARDWARE.classify(
            {'vendor': 'nvidia', 'name': 'Synthetic GPU', 'vram_gb': 6},
            64, 100, wsl=False, rosetta=False)
        self.assertEqual((verdict, route), ('marginal', 'nvidia'))


if __name__ == '__main__':
    unittest.main()
