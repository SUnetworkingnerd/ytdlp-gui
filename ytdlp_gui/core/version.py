"""Installed and latest yt-dlp versions (no Qt)."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request

from .runner import hidden_run_kwargs

RELEASES_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """'2025.06.30' or 'yt-dlp 2025.06.30.123' -> (2025, 6, 30, 123). Empty tuple if none."""
    match = re.search(r"\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)*", text or "")
    return tuple(int(p) for p in match.group(0).split(".")) if match else ()


def is_outdated(installed: str, latest: str) -> bool:
    """True if ``installed`` is older than the release ``latest`` (date-based versions).

    Builds from the master branch carry the version of the last release (or a later
    date), so they never count as outdated against that same release.
    """
    have, want = parse_version(installed), parse_version(latest)
    return bool(have and want) and have[:3] < want[:3]


def installed_version(base: list[str], timeout: int = 60) -> str:
    """Runs ``yt-dlp --version`` with the given command prefix. Raises on failure."""
    proc = subprocess.run([*base, "--version"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          **hidden_run_kwargs())
    out = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not out:
        err = proc.stderr.strip().splitlines()
        raise RuntimeError(err[-1] if err else f"yt-dlp exited with {proc.returncode}")
    return out[-1].strip()


def latest_release(timeout: int = 10, opener=urllib.request.urlopen) -> str:
    """Tag of the newest yt-dlp release on GitHub, e.g. '2025.06.30'. Raises on failure."""
    req = urllib.request.Request(RELEASES_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ytdlp-gui"})
    with opener(req, timeout=timeout) as resp:
        return str(json.load(resp)["tag_name"])


def pip_update_commands(python: str, spec: str) -> list[list[str]]:
    """Two pip runs: reinstall only yt-dlp itself (fresh master), then top up its
    dependencies only where needed. A plain --force-reinstall would reinstall every
    dependency each time, which is slow and can break a working environment."""
    return [
        [python, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", spec],
        [python, "-m", "pip", "install", "--upgrade-strategy", "only-if-needed", spec],
    ]
