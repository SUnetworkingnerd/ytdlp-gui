"""Quality choice on the Download tab (no Qt).

yt-dlp already prefers the highest resolution, so "Best available" downloads 4K (or 8K)
when a video has it. The other choices cap the resolution with ``-S res:N``.

One trap: yt-dlp's ``-t mp4`` preset sorts by ``vcodec:h264`` first, and YouTube only
offers H.264 up to 1080p, so "MP4" alone never gives 4K. A ``-S`` given after the preset
takes priority over the preset's own sort order, so a resolution choice wins and the MP4
then holds VP9 or AV1 video (YouTube's codecs for 1440p and up).
"""
from __future__ import annotations

# (label, max height or None, yt-dlp args). Args go after the container preset.
QUALITIES = [
    ("Best available (4K or 8K when the video has it)", None, []),
    ("4K (2160p)", 2160, ["-S", "res:2160"]),
    ("1440p", 1440, ["-S", "res:1440"]),
    ("1080p", 1080, ["-S", "res:1080"]),
    ("720p", 720, ["-S", "res:720"]),
    ("480p", 480, ["-S", "res:480"]),
]
BEST, FOUR_K = 0, 1


def quality_args(index: int, preset_args: list[str]) -> list[str]:
    """Args for the chosen quality. With yt-dlp's MP4 preset, "Best" also needs ``-S res``,
    or the preset's H.264 preference would quietly cap the download at 1080p."""
    if not 0 <= index < len(QUALITIES):
        index = BEST
    args = list(QUALITIES[index][2])
    if not args and is_mp4_preset(preset_args):
        args = ["-S", "res"]
    return args


def is_mp4_preset(preset_args: list[str]) -> bool:
    return preset_args == ["-t", "mp4"]


def is_audio_preset(preset_args: list[str]) -> bool:
    return preset_args in (["-t", "mp3"], ["-t", "aac"])


def max_height(formats: list[dict]) -> int:
    """Highest video height among fetched formats (0 if none)."""
    return max((int(f.get("height") or 0) for f in formats
                if f.get("vcodec") != "none"), default=0)


def quality_note(index: int, preset_args: list[str], have_ffmpeg: bool, have_js: bool,
                 available: int = 0) -> str:
    """Short hint under the Quality box ("" when there is nothing to say)."""
    if is_audio_preset(preset_args):
        return ""
    cap = QUALITIES[index][1] if 0 <= index < len(QUALITIES) else None
    notes = []
    if available and cap and available < cap:
        notes.append(f"This video only goes up to {available}p, so that is what you will get.")
    elif available and not cap:
        notes.append(f"This video goes up to {available}p.")
    # Above 1080p is where YouTube switches to VP9/AV1 and separate streams.
    high = (cap is None or cap > 1080) and not (available and available <= 1080)
    if high and is_mp4_preset(preset_args):
        notes.append("YouTube has no H.264 above 1080p, so above that the MP4 holds VP9 or AV1 "
                     "video. Modern players handle it; some old devices and editors don't.")
    if high and not have_ffmpeg:
        notes.append("4K needs ffmpeg: YouTube sends 1440p and up as separate video and audio. "
                     "Without it you get a lower quality file.")
    if high and not have_js:
        notes.append("Install deno: without a JavaScript runtime YouTube may only offer low "
                     "resolutions.")
    return " ".join(notes)
