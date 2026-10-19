"""Regression guard for #141333: a Bot Mode group-room turn prompt is harness-
synthesized (Desktop's group-round-prompt.ts) — participation header, room
delta and "Rules for this room" — not the member user's speech, but it lands
as the session's user row. Before this fix it reached memory providers through
every ingestion entry point (turn tick, prefetch, queued prefetch, turn sync),
so providers that model the user stored the harness text verbatim as user
speech and derived junk conclusions from it. The manager now keeps such turns
out of provider ingestion entirely.
"""
from agent.memory_manager import MemoryManager
from agent.memory_provider import MemoryProvider

GROUP_ROOM_PROMPT = (
    '[Group chat: "Skill Handoff"] You are @bobby, one participant in a group chat '
    "with @hermes and the user.\n\n"
    "  user: please summarize the deploy\n"
    "  @hermes: (pass)\n"
    "\n"
    "Rules for this room:\n"
    "- Reply with ONE conversational message ONLY if you have something new worth adding."
)


class _RecordingProvider(MemoryProvider):
    """Records every ingestion entry point it is handed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "recording"

    def initialize(self, session_id: str = "", **kwargs) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def system_prompt_block(self) -> str:
        return ""

    def get_tool_schemas(self) -> list[dict]:
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self.calls.append(("prefetch", query))
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self.calls.append(("queue_prefetch", query))

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages=None, turn_author=None) -> None:
        self.calls.append(("sync_turn", user_content))

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self.calls.append(("on_turn_start", message))


def _manager() -> tuple[MemoryManager, _RecordingProvider]:
    mgr = MemoryManager()
    provider = _RecordingProvider()
    mgr.add_provider(provider)
    return mgr, provider


def test_group_room_turn_prompt_stays_out_of_provider_ingestion():
    mgr, provider = _manager()

    mgr.on_turn_start(1, GROUP_ROOM_PROMPT)
    assert mgr.prefetch_all(GROUP_ROOM_PROMPT, session_id="s1") == ""
    mgr.queue_prefetch_all(GROUP_ROOM_PROMPT, session_id="s1")
    mgr.sync_all(GROUP_ROOM_PROMPT, "the member bot's reply", session_id="s1")

    assert mgr.flush_pending(timeout=10) is True
    assert provider.calls == []


def test_user_turns_still_reach_providers():
    mgr, provider = _manager()

    mgr.on_turn_start(1, "regular user text")
    mgr.prefetch_all("regular user text", session_id="s1")
    mgr.queue_prefetch_all("regular user text", session_id="s1")
    mgr.sync_all("regular user text", "assistant answer", session_id="s1")

    assert mgr.flush_pending(timeout=10) is True
    kinds = {kind for kind, _ in provider.calls}
    assert {"on_turn_start", "prefetch", "queue_prefetch", "sync_turn"} <= kinds
    assert all(text == "regular user text" for _, text in provider.calls)
