"""Settings and yt-dlp arguments for the "Archive full channel" feature (no Qt)."""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, fields

# (label, extra yt-dlp args). The last entry is audio only.
QUALITIES = [
    ("Best available", []),
    ("Up to 2160p (4K)", ["-S", "res:2160"]),
    ("Up to 1440p", ["-S", "res:1440"]),
    ("Up to 1080p", ["-S", "res:1080"]),
    ("Up to 720p", ["-S", "res:720"]),
    ("Up to 480p", ["-S", "res:480"]),
    ("Audio only (best quality)", ["-f", "ba/b", "-x"]),
]
AUDIO_ONLY = len(QUALITIES) - 1

# One folder per channel, files sorted by upload date (YYYYMMDD), id keeps names unique.
OUTPUT_TEMPLATE = "%(channel,uploader)s/%(upload_date)s - %(title)s [%(id)s].%(ext)s"

_YT_CHANNEL = re.compile(
    r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))"
    r"/?(?:[?#].*)?$", re.I)


@dataclass
class ArchiveSettings:
    url: str = ""
    folder: str = ""
    quality: int = 0
    include_shorts_live: bool = True   # YouTube: Shorts and live stream recordings too
    metadata: bool = True              # embedded tags/chapters, info.json, thumbnail
    subtitles: bool = True             # all manually uploaded subtitles (no live chat)
    gentle: bool = True                # random pauses so big channels don't get rate limited
    quick_update: bool = False         # only fetch what is newer than the archive

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ArchiveSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def channel_urls(url: str, include_shorts_live: bool = True, quick_update: bool = False) -> list[str]:
    """URLs to hand to yt-dlp. Non-channel URLs (playlists, other sites) pass through.

    A plain YouTube channel URL already covers Videos, Shorts and Live. For a quick
    update each tab is its own input, because --break-on-existing would otherwise stop
    at the first known video of the first tab and skip the other tabs.
    """
    url = url.strip()
    match = _YT_CHANNEL.match(url)
    if not match:
        return [url]
    base = match.group(1)
    if quick_update:
        tabs = ["videos"] + (["shorts", "streams"] if include_shorts_live else [])
        return [f"{base}/{tab}" for tab in tabs]
    return [base] if include_shorts_live else [f"{base}/videos"]


def archive_name(url: str) -> str:
    """File name of the download archive; one archive file per channel."""
    url = url.strip()
    match = _YT_CHANNEL.match(url)
    if match:
        seg = match.group(1).rsplit("/", 1)[-1]
    else:
        seg = re.sub(r"^https?://(?:www\.)?", "", url).strip("/")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", seg.lstrip("@")).strip("._")[:80] or "channel"
    return f"{slug}.archive.txt"


def build_archive(s: ArchiveSettings) -> tuple[list[str], list[str]]:
    """Returns (yt-dlp options, URLs)."""
    quality = s.quality if 0 <= s.quality < len(QUALITIES) else 0
    audio = quality == AUDIO_ONLY
    args: list[str] = []
    if s.folder:
        args += ["-P", s.folder]
    args += ["-o", OUTPUT_TEMPLATE, "--trim-filenames", "180",
             "--download-archive", os.path.join(s.folder, archive_name(s.url)),
             "--lazy-playlist"]
    args += QUALITIES[quality][1]
    if not audio:
        args += ["--merge-output-format", "mkv"]
    if s.metadata:
        args += ["--embed-metadata", "--embed-chapters", "--write-info-json",
                 "--no-write-playlist-metafiles", "--write-thumbnail",
                 "--convert-thumbnails", "jpg"]
    if s.subtitles:
        args += ["--write-subs", "--sub-langs", "all,-live_chat"]
    if s.gentle:
        args += ["--sleep-requests", "1", "--sleep-interval", "3", "--max-sleep-interval", "8"]
        if s.subtitles:
            args += ["--sleep-subtitles", "2"]
    if s.quick_update:
        args += ["--break-on-existing", "--break-per-input"]
    return args, channel_urls(s.url, s.include_shorts_live, s.quick_update)
