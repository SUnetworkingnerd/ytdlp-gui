"""Fetch video/playlist metadata via ``yt-dlp --dump-single-json`` (no Qt)."""
from __future__ import annotations

import json
import subprocess

from .runner import hidden_run_kwargs

# Options that change *how yt-dlp reaches the site*. They are passed along to the
# metadata fetch so that cookies, proxies, impersonation, etc. also apply there.
PASS_THROUGH = {
    "--proxy", "--socket-timeout", "--source-address", "--impersonate",
    "--force-ipv4", "--force-ipv6", "--no-check-certificates",
    "--legacy-server-connect", "--add-headers", "--geo-verification-proxy",
    "--xff", "--username", "--password",
    "--twofactor", "--netrc", "--netrc-location", "--netrc-cmd",
    "--video-password", "--client-certificate", "--client-certificate-key",
    "--client-certificate-password", "--extractor-args", "--js-runtimes",
    "--no-js-runtimes", "--remote-components", "--no-remote-components",
    "--plugin-dirs", "--no-plugin-dirs", "--ignore-config", "--config-locations",
    "--use-extractors", "--default-search",
}

LIVE_HINTS = {
    "is_live": "This stream is live now. Tick \"Record from the beginning\" to "
               "capture it from the start (supported sites only, experimental).",
    "is_upcoming": "Scheduled stream. Tick \"Wait for scheduled stream\" so yt-dlp "
                   "keeps retrying until it starts.",
    "post_live": "The stream has ended and the recording is still being processed.",
    "was_live": "This was a live stream and is now a normal video.",
}


def fetch_info(url: str, base: list[str], extra_args: list[str] | None = None,
               timeout: int = 180) -> dict:
    cmd = [*base, "--dump-single-json", "--no-warnings", "--flat-playlist",
           "--ignore-no-formats-error", "--color", "never",
           *(extra_args or []), "--", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **hidden_run_kwargs())
    out = proc.stdout.strip()
    if not out:
        lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
        raise RuntimeError(lines[-1] if lines else f"yt-dlp exited with {proc.returncode}")
    return json.loads(out.splitlines()[0])
