"""Offline public-CLI regressions. MIT; preserves FurkanL0's domain-intel implementation.

Run from a Hermes checkout: python -B -m unittest discover -s tests/skills -p test_domain_intel_signals.py -v
Or set DOMAIN_INTEL_SCRIPT to a standalone domain_intel.py and run this file.
Only socket/TLS/HTTP adapters are synthetic; CLI parsing, WHOIS parsing, verdict,
bulk scheduling and JSON serialization are production code. No network access.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch


def fixture_main(script, argv, scenario):
    spec = importlib.util.spec_from_file_location('domain_intel_candidate', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def getaddrinfo(domain, port, family):
        calls.append(['dns', domain])
        if scenario.get('a'):
            return [(family, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 0))]
        raise OSError('synthetic DNS failure')

    def urlopen(request, **kwargs):
        calls.append(['doh', request.full_url])
        if scenario.get('ns'):
            return io.BytesIO(b'{"Answer":[{"data":"ns.example.test"}]}')
        if scenario.get('empty_dns'):
            return io.BytesIO(b'{"Answer":[]}')
        raise OSError('synthetic DoH failure')

    class WhoisSocket:
        def __init__(self):
            self.data = scenario.get('whois', '').encode()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def sendall(self, value):
            calls.append(['whois_query', value.decode().strip()])
        def recv(self, size):
            data, self.data = self.data, b''
            return data

    def connection(address, **kwargs):
        host, port = address
        calls.append(['connect', host, port])
        if port == 43 and 'whois' in scenario:
            return WhoisSocket()
        if port == 443 and scenario.get('tls'):
            return contextlib.nullcontext(object())
        raise OSError('synthetic connection failure')

    class TLSContext:
        def wrap_socket(self, sock, **kwargs):
            return contextlib.nullcontext(object())

    with patch.object(module.socket, 'getaddrinfo', getaddrinfo), \
         patch.object(module.socket, 'create_connection', connection), \
         patch.object(module.urllib.request, 'urlopen', urlopen), \
         patch.object(module.ssl, 'create_default_context', TLSContext), \
         patch.object(sys, 'argv', [str(script)] + argv):
        module.main()
    print(json.dumps(calls), file=sys.stderr)


class DomainIntelSignals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default = Path(__file__).resolve().parents[2] / 'optional-skills/research/domain-intel/scripts/domain_intel.py'
        cls.script = Path(os.environ.get('DOMAIN_INTEL_SCRIPT', default)).resolve()
        if not cls.script.is_file():
            raise RuntimeError('Set DOMAIN_INTEL_SCRIPT or run from a Hermes checkout')

    def cli(self, argv, scenario=None):
        result = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).resolve()), '--fixture', str(self.script), json.dumps(argv)],
            input=json.dumps(scenario or {}), text=True, capture_output=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), json.loads(result.stderr)

    def assert_signal(self, scenario, expected):
        result, calls = self.cli(['available', 'fixture.com'], scenario)
        self.assertEqual(result['verdict'], expected)
        self.assertEqual(result['confidence'], 'uncalibrated')
        self.assertEqual(result['domain'], 'fixture.com')
        self.assertIn(['dns', 'fixture.com'], calls)
        self.assertTrue(any(c[0] == 'doh' for c in calls))
        return result

    def test_all_observations_fail_is_unknown(self):
        result = self.assert_signal({}, 'UNKNOWN — failed or inconclusive observations are not absence')
        self.assertIsNone(result['signals']['whois_available'])
        self.assertIn('synthetic connection failure', result['signals']['whois_note'])

    def test_inconclusive_whois_is_unknown(self):
        self.assert_signal({'whois': 'Temporary service message'}, 'UNKNOWN — failed or inconclusive observations are not absence')

    def test_unsupported_tld_is_unknown(self):
        result, _ = self.cli(['available', 'fixture.invalid'])
        self.assertEqual(result['verdict'], 'UNKNOWN — failed or inconclusive observations are not absence')
        self.assertEqual(result['confidence'], 'uncalibrated')

    def test_no_match_is_signal_not_purchase_claim(self):
        result = self.assert_signal({'whois': 'No match for fixture.com', 'empty_dns': True}, 'WHOIS NO-MATCH SIGNAL — availability/price require registrar check')
        self.assertIs(result['signals']['whois_available'], True)

    def test_registered_whois_is_preserved(self):
        result = self.assert_signal({'whois': 'Registrar: Example Registrar'}, 'WHOIS REGISTERED SIGNAL — verify current registrar status')
        self.assertIs(result['signals']['whois_available'], False)

    def test_dns_a_presence_is_preserved(self):
        result = self.assert_signal({'a': True}, 'NETWORK PRESENCE SIGNAL — registration status unverified')
        self.assertEqual(result['signals']['dns_a'], ['192.0.2.1'])

    def test_ns_presence_is_preserved(self):
        result = self.assert_signal({'ns': True}, 'NETWORK PRESENCE SIGNAL — registration status unverified')
        self.assertEqual(result['signals']['dns_ns'], ['ns.example.test'])

    def test_tls_overrides_conflicting_no_match(self):
        result = self.assert_signal({'tls': True, 'whois': 'No match'}, 'NETWORK PRESENCE SIGNAL — registration status unverified')
        self.assertIs(result['signals']['ssl_reachable'], True)
        self.assertIs(result['signals']['whois_available'], True)

    def test_bulk_boundaries_and_omission(self):
        for size in [1, 20, 21, 23]:
            with self.subTest(size=size):
                domains = [f'fixture{i}.com' for i in range(size)]
                result, calls = self.cli(['bulk'] + domains + ['--checks', 'available'])
                self.assertEqual(result['total'], min(size, 20))
                self.assertEqual(len(result['results']), min(size, 20))
                self.assertEqual(result['requested_total'], size)
                self.assertEqual(result['has_more'], size > 20)
                self.assertEqual(result['omitted_domains'], domains[20:])
                self.assertEqual(result['checks'], ['available'])
                self.assertCountEqual([r['domain'] for r in result['results']], domains[:20])
                self.assertCountEqual([c[1] for c in calls if c[0] == 'dns'], domains[:20])

    def test_bulk_duplicates_preserve_input_positions(self):
        domains = ['duplicate.com'] * 21
        result, calls = self.cli(['bulk'] + domains + ['--checks', 'available'])
        self.assertEqual(result['requested_total'], 21)
        self.assertEqual(result['total'], 20)
        self.assertEqual(result['omitted_domains'], ['duplicate.com'])
        self.assertEqual(len([c for c in calls if c[0] == 'dns']), 20)

    def test_existing_dns_cli_still_works(self):
        result, _ = self.cli(['dns', 'fixture.com'], {'a': True, 'empty_dns': True})
        self.assertEqual(result['records']['A'], ['192.0.2.1'])
        self.assertEqual(result['records']['MX'], [])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--fixture':
        fixture_main(sys.argv[2], json.loads(sys.argv[3]), json.load(sys.stdin))
    else:
        unittest.main()
