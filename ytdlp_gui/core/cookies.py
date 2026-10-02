"""Turns the friendly "use cookies" choice into yt-dlp arguments (no Qt)."""
from __future__ import annotations

# Browsers yt-dlp can read cookies from on Linux and Windows.
BROWSERS = ["firefox", "chrome", "chromium", "edge", "brave", "vivaldi", "opera", "whale"]

MODES = ("none", "browser", "file")


def cookie_args(mode: str, browser: str = "", profile: str = "", path: str = "") -> list[str]:
    """mode: "none", "browser" (--cookies-from-browser) or "file" (--cookies)."""
    if mode == "browser" and browser:
        profile = profile.strip()
        spec = f"{browser}:{profile}" if profile else browser
        return [f"--cookies-from-browser={spec}"]
    if mode == "file" and path.strip():
        return [f"--cookies={path.strip()}"]
    return []
