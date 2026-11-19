"""Rules for picking rows in the formats table (no Qt).

yt-dlp merges at most one video and one audio stream unless --video-multistreams /
--audio-multistreams are set, so ``137+140+251`` fails. The table therefore keeps at most
one video-only and one audio-only row, or a single row that already has both.
"""
from __future__ import annotations

VIDEO, AUDIO, BOTH = "video", "audio", "both"


def format_kind(fmt: dict) -> str:
    """VIDEO (video only), AUDIO (audio only) or BOTH (has both, or unknown)."""
    vcodec, acodec = fmt.get("vcodec"), fmt.get("acodec")
    if vcodec == "none" and acodec != "none":
        return AUDIO
    if acodec == "none" and vcodec != "none":
        return VIDEO
    return BOTH


def pick_formats(selected: list[tuple[str, str]], newest: str | None) -> list[tuple[str, str]]:
    """Reduce a selection to a valid one.

    ``selected`` is the (format_id, kind) of every selected row, ``newest`` the id the user
    just clicked. The newest row always stays; it replaces any earlier row it conflicts with.
    Returns the rows to keep, video first.
    """
    by_id = dict(selected)
    order = [fid for fid, _k in selected if fid != newest]
    if newest in by_id:
        order.append(newest)                       # newest wins conflicts
    keep: dict[str, str] = {}                      # kind -> format id
    for fid in order:
        kind = by_id[fid]
        if kind == BOTH:
            keep = {BOTH: fid}
        else:
            keep.pop(BOTH, None)
            keep[kind] = fid
    return [(keep[k], k) for k in (BOTH, VIDEO, AUDIO) if k in keep]


def format_string(rows: list[tuple[str, str]]) -> str:
    return "+".join(fid for fid, _k in rows)
