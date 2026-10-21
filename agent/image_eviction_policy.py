"""Send-path image eviction policy shared by both stateless outbound passes.

Two passes retire old tool-result images from the per-request copy of the conversation:
``agent.context_compressor.evict_stale_outbound_tool_images`` on the OpenAI-shaped list and
``agent.anthropic_message_convert._evict_old_screenshots`` on the Anthropic wire list. Both run
from scratch on a fresh clone every request, so they must agree on one policy or the second
pass re-evicts on a different frontier than the first (#113517). This module is stdlib-only so
the wire converter, a leaf, can import it without dragging in the compaction stack.

Why the trigger is a provider limit, not a keep-newest count: retiring an image edits a message
the provider has already cached, and Anthropic matches its prompt cache on an exact byte prefix.
A keep-newest-N window retires one more message on every new image, so every turn is a
full-prefix miss. Holding images until the request would cross a real API limit and then
retiring a batch costs one slower turn per batch and nothing below the limit.

20 is the documented threshold at which Anthropic applies a stricter per-image dimension cap
(2000 px) to EVERY image in the request, counting images nested in tool_result content. Crossing
it costs nothing by itself: it only matters when some image actually exceeds 2000 px on a side.
Hermes already shrinks its own tool images far below that before they enter history
(``vision_analyze`` and browser screenshots embed at 1568 px, ``computer_use`` at 1456 px by
default), so the 21st screenshot of a run trips no provider constraint and a batch retire there
buys a full prefix rewrite for nothing — and, on the preserved-thinking models, invalidates every
later thinking block. Anthropic's own computer-use guidance now says to keep screenshots at or
under 2000 px and NOT to prune them client-side for exactly that reason.

So the block ceiling is CONDITIONAL on the request's actual dimensions: the strict 20 applies
only when some image exceeds ``MANY_IMAGE_DIMENSION_LIMIT``, when the stricter per-side cap is
live and the retire genuinely protects the request; otherwise the ceiling is the hard count
``OUTBOUND_IMAGE_HARD_LIMIT``. The hard ceilings above 20 are real but distant (100 images per
request on 200K-context models, 600 otherwise) and the 32 MB request-size limit usually binds
first, which the byte budget guards with headroom for text. Classification reads image HEADERS
only — never a full decode of every base64 payload per request — and treats unreadable or unknown
dimensions as unsafe, so a payload this module cannot measure keeps today's strict trigger.
"""

from __future__ import annotations

import base64
import math
import re
import struct
from typing import Any, Iterable, Optional, Sequence

OUTBOUND_IMAGE_LIMIT = 20
# Hard per-request image count on 200K-context Claude models; the ceiling once the many-image
# stricter cap is provably not live (see module docstring). Deliberately not 600 — the byte
# budget below binds first for a 200K-context transcript, and the byte ceiling needs no context
# knowledge to be safe.
OUTBOUND_IMAGE_HARD_LIMIT = 100
OUTBOUND_IMAGE_BUDGET_BYTES = 24_000_000
IMAGE_EVICTION_BATCH = 8
# Satisfiability floor — see outbound_image_retire_count.
OUTBOUND_IMAGE_FLOOR = 3

# Anthropic's stricter many-image per-side cap (px): above it, every image in the request is held
# to this limit, so an image over it keeps the strict trigger live.
MANY_IMAGE_DIMENSION_LIMIT = 2000
# Bytes of an embedded image to decode when reading its header. PNG IHDR ends at byte 24; a JPEG
# SOF marker lives within the first few KB. ~32 KB of base64 is generous headroom, and only the
# prefix is ever decoded (base64 expands 3 bytes per 4 chars).
_DIMENSION_PROBE_CHARS = 32 * 1024
_JPEG_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def image_dimensions_from_bytes(raw: bytes) -> Optional[tuple[int, int]]:
    """(width, height) for PNG / JPEG bytes, or None when unreadable. PNG: IHDR. JPEG: walk
    segments (skipping 0xFF fill bytes) to the first SOF marker; stop at SOS. Lives here so both
    the send-path passes and the tool layer share one header-only reader."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])  # cannot fail: 8 bytes are guaranteed present
        return int(width), int(height)
    if raw.startswith(b"\xff\xd8") and len(raw) > 4:
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker, i = raw[i + 1], i + 2
            while marker == 0xFF and i < len(raw):
                marker, i = raw[i], i + 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA or i + 2 > len(raw):
                break
            segment_len = int.from_bytes(raw[i:i + 2], "big")
            if segment_len < 2 or i + segment_len > len(raw):
                break
            if marker in _JPEG_SOF_MARKERS and segment_len >= 7:
                return int.from_bytes(raw[i + 5:i + 7], "big"), int.from_bytes(raw[i + 3:i + 5], "big")
            i += segment_len
    return None


def image_max_side_from_data(data: Optional[str]) -> Optional[int]:
    """Longest side (px) of a data-URL / bare-base64 image, or None when it cannot be read.

    Decodes only the leading base64 prefix (``_DIMENSION_PROBE_CHARS``), so classifying a request
    with dozens of images costs a few header reads, not a full decode of every payload. Accepts
    both ``data:image/...;base64,....`` and a bare base64 string; anything else (a remote URL, an
    unrecognized container) yields None.
    """
    if not data:
        return None
    payload = data
    if payload.startswith("data:"):
        _, separator, payload = payload.partition(",")
        if not separator or ";base64" not in data[:data.index(",")]:
            return None
    payload = payload.strip()
    prefix = payload[:_DIMENSION_PROBE_CHARS]
    prefix = prefix[: len(prefix) - (len(prefix) % 4)]  # decode needs a multiple-of-four length
    if not prefix:
        return None
    try:
        raw = base64.b64decode(prefix, validate=False)
    except (ValueError, TypeError):
        return None
    dims = image_dimensions_from_bytes(raw)
    return max(dims) if dims is not None else None


def image_within_many_image_limit(data: Optional[str]) -> bool:
    """True only when the image's longest side is known and within ``MANY_IMAGE_DIMENSION_LIMIT``.

    Unknown dimensions are NOT safe: the stricter per-request cap would still be live, so the
    caller keeps the strict trigger. See :func:`images_all_within_many_image_limit`.
    """
    longest = image_max_side_from_data(data)
    return longest is not None and longest <= MANY_IMAGE_DIMENSION_LIMIT


def images_all_within_many_image_limit(datas: Iterable[Optional[str]]) -> bool:
    """True when EVERY image in the request is a readable image within the many-image limit.

    The Anthropic rule is per request: one oversized image holds every image in the request to the
    stricter cap, so a single unreadable or over-limit image must keep the strict block ceiling.
    Short-circuits on the first offender, which is what keeps this cheap on image-heavy turns.
    """
    for data in datas:
        if not image_within_many_image_limit(data):
            return False
    return True


def outbound_image_block_limit(many_image_safe: bool) -> int:
    """Block ceiling for a request: the strict 20 unless every image is within the many-image cap."""
    return OUTBOUND_IMAGE_HARD_LIMIT if many_image_safe else OUTBOUND_IMAGE_LIMIT


def outbound_image_retire_count(
    carrier_blocks_newest_first: Sequence[int],
    reserved_blocks: int,
    *,
    carrier_bytes_newest_first: Optional[Sequence[int]] = None,
    reserved_bytes: int = 0,
    many_image_safe: bool = False,
    limit: Optional[int] = None,
    budget: int = OUTBOUND_IMAGE_BUDGET_BYTES,
    batch: int = IMAGE_EVICTION_BATCH,
    floor: int = OUTBOUND_IMAGE_FLOOR,
) -> int:
    """How many of the OLDEST image-bearing tool results to retire.

    ``carrier_blocks_newest_first`` is the image-block count per image-bearing tool result,
    newest first; ``reserved_blocks`` counts images the pass must never rewrite (user
    uploads). The byte dimension is active only when ``carrier_bytes_newest_first`` is
    given. ``limit`` defaults to :func:`outbound_image_block_limit` of ``many_image_safe``,
    so both passes pick the same ceiling from the same per-request classification.

    The retire count must be a STEP FUNCTION of the overshoot, because the pass is recomputed
    on every request: an exact ``count - limit`` target moves the frontier on every new image,
    and a fixed one-batch retire stops enforcing the limit after the first batch. So the count
    advances in quanta until the request fits.

    The quantum is ``batch`` capped at ``window - floor``, where ``window`` is how many newest
    carriers fit under the ceiling. A quantum wider than that would step past the newest frames
    on every advance; cutting each step back to exactly ``total - floor`` instead makes the
    retire count track ``total`` again — the per-image frontier this policy exists to avoid,
    visible whenever a tool result carries several images or uploads fill most of the ceiling.
    Holding for ``window - floor`` turns per advance is the most the floor allows.

    The floor is a SATISFIABILITY floor: it shelters the newest frames only when reserved
    uploads alone breach the block ceiling (no retirement can fix that), and never under byte
    pressure — the request-size limit is hard and the provider answers 413. Even then the
    count stays a step function: it retires whole quanta down toward the floor (keeping
    ``floor`` to ``floor + batch - 1`` carriers, fewer when the byte budget binds), so the
    cached prefix is rewritten once per quantum rather than on every new image.
    """
    if limit is None:
        limit = outbound_image_block_limit(many_image_safe)
    total = len(carrier_blocks_newest_first)
    sizes = carrier_bytes_newest_first
    return max(
        (
            _retire_count_for_request(
                carrier_blocks_newest_first[total - m:],
                reserved_blocks,
                None if sizes is None else sizes[total - m:],
                reserved_bytes,
                limit,
                budget,
                batch,
                floor,
            )
            for m in range(1, total + 1)
        ),
        default=0,
    )



def outbound_retired_image_ids(agent: Any) -> set[str]:
    """The session's latched set of tool-call ids whose images the send path has retired.

    Keyed on ``agent.session_id`` so a new session (or a compaction rotation, which rewrites the
    prefix anyway) starts clean and a reused provider call id cannot retire a fresh screenshot.
    """
    session_id = getattr(agent, "session_id", None)
    held = getattr(agent, "_outbound_retired_image_ids", None)
    if held is None or held[0] != session_id:
        held = (session_id, set())
        agent._outbound_retired_image_ids = held
    return held[1]


def latched_retire_count(
    retire: int, carrier_ids_newest_first: Sequence[Optional[str]], retired_ids: Optional[set[str]]
) -> int:
    """Widen ``retire`` so every carrier up to the newest previously retired one stays retired,
    then record the carriers being retired into ``retired_ids``.

    The per-request decision cannot see history: a request that drops an upload (once it leaves
    the replay window) relaxes the ceiling, and re-deciding from scratch would restore
    screenshots an earlier request already retired, rewriting the cached prefix back.
    """
    if retired_ids is None:
        return retire
    total = len(carrier_ids_newest_first)
    latched = [n for n, cid in enumerate(carrier_ids_newest_first) if cid and cid in retired_ids]
    retire = max(retire, total - latched[0]) if latched else retire
    retired_ids.update(cid for cid in carrier_ids_newest_first[total - retire:] if cid)
    return retire

def _retire_count_for_request(
    carrier_blocks_newest_first: Sequence[int],
    reserved_blocks: int,
    sizes: Optional[Sequence[int]],
    reserved_bytes: int,
    limit: int,
    budget: int,
    batch: int,
    floor: int,
) -> int:
    """Retire count for one request in isolation; see :func:`outbound_image_retire_count`."""
    total = len(carrier_blocks_newest_first)
    blocks_kept = [0] * (total + 1)
    bytes_kept = [0] * (total + 1)
    for i in range(total):
        blocks_kept[i + 1] = blocks_kept[i] + carrier_blocks_newest_first[i]
        bytes_kept[i + 1] = bytes_kept[i] + (sizes[i] if sizes is not None else 0)

    def _bytes_fit(kept: int) -> bool:
        return sizes is None or reserved_bytes + bytes_kept[kept] <= budget

    def _fits(kept: int) -> bool:
        return reserved_blocks + blocks_kept[kept] <= limit and _bytes_fit(kept)

    if _fits(total):
        return 0

    floor = min(max(floor, 0), total)
    if not _fits(0) and _bytes_fit(floor):
        # Reserved uploads alone breach the block ceiling: no retirement can make the request
        # fit it, so shelter the floor but keep advancing in whole quanta. Retiring down to
        # exactly ``total - floor`` here moves the frontier on every new image (#133999). The
        # quantum is capped by how many carriers the byte budget lets us keep, so byte
        # pressure narrows the step instead of collapsing it back to the floor.
        byte_window = max(k for k in range(floor, total + 1) if _bytes_fit(k))
        quantum = max(1, min(batch, byte_window - floor + 1))
        excess = total - floor
        return excess - excess % quantum
    window = max(k for k in range(total + 1) if _fits(k)) if _fits(0) else 0
    quantum = max(1, min(batch, window - floor))

    retire = 0
    while retire < total:
        retire = min(retire + quantum, total)
        if _fits(total - retire):
            break
    return retire


# ─── Image part / payload readers (shared with the send-path passes) ────────
# Moved from agent/context_compressor.py (the part taxonomy) and
# agent/turn_recovery.py (the dimension reader) so both eviction passes, the
# recovery path and the tool layer share one set of image-error primitives;
# still pure stdlib.

_IMAGE_PART_TYPES = frozenset({"image_url", "input_image", "image"})


def _is_image_part(part: Any) -> bool:
    """True if ``part`` is an image block (``image_url``, ``input_image``, or ``image``)."""
    return isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES


def _tool_result_parts(content: Any) -> Any:
    """Part list of a tool-result body, unwrapping the ``_multimodal`` envelope."""
    return content.get("content") if isinstance(content, dict) and content.get("_multimodal") else content


def _image_payload(msg: dict[str, Any]) -> list[str]:
    """Data URL / base64 string of each image block in a message (``""`` when unmeasurable).

    The provider counts BLOCKS (one ``tool_result`` with three screenshots is three), so the
    block count is the list length. Bytes are the summed string lengths: the payload is ASCII and
    the JSON framing around it is noise against a 24 MB budget. The strings also let the caller
    read dimensions from headers only, without a second walk or a full decode.
    """
    parts = _tool_result_parts(msg.get("content"))
    if not isinstance(parts, list):
        return []
    sources: list[str] = []
    for p in parts:
        if not _is_image_part(p):
            continue
        image_url = p.get("image_url")
        source = p.get("source")
        data = (
            (image_url.get("url") if isinstance(image_url, dict) else image_url)
            or (source.get("data") if isinstance(source, dict) else None)
        )
        sources.append(data if isinstance(data, str) else "")
    return sources


def provider_image_count_cap(agent: Any) -> Optional[int]:
    """The per-request image COUNT cap a route's rejection advertised (#142602).

    ``None`` until the count-cap recovery latches one, in which case the send-path
    passes apply it over the dimension-derived default ceiling — the static
    ``OUTBOUND_IMAGE_LIMIT`` cannot know a route caps at 16.
    """
    cap = getattr(agent, "_provider_image_count_cap", None)
    return cap if isinstance(cap, int) and cap > 0 else None


def _image_error_max_dimension(error: Exception) -> Optional[int]:
    """Extract a provider-reported image dimension ceiling, if present."""
    parts = []
    for value in (error, getattr(error, "message", None), getattr(error, "body", None)):
        if value:
            try:
                parts.append(str(value))
            except Exception:
                pass
    text = " ".join(parts).lower()
    # OpenAI Codex Responses reports a tile-patch budget (ceil(w/32)×ceil(h/32))
    # instead of a pixel ceiling. A square image is the worst case for the budget,
    # so a per-side cap of isqrt(limit)*32 px keeps isqrt(limit)² ≤ limit — for the
    # 30000-patch ceiling that is 5536 px. Without this the caller falls back to
    # 8000 px and a 6000 px image that already exceeds the budget is skipped (#106337).
    if "patches after processing" in text:
        match = re.search(r"exceeding the limit of\s*(\d{2,7})", text)
        if not match:
            return None
        max_dimension = math.isqrt(int(match.group(1))) * 32
        return max_dimension if 512 <= max_dimension <= 8000 else None
    if "image" not in text or "dimension" not in text or "max allowed size" not in text:
        return None
    match = re.search(r"max allowed size(?:\s+for [^:]+)?:\s*(\d{3,5})\s*pixels?", text)
    if not match:
        return None
    try:
        max_dimension = int(match.group(1))
    except ValueError:
        return None
    return max_dimension if 512 <= max_dimension <= 8000 else None
