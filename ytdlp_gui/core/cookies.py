"""Turns the friendly "use cookies" choice into yt-dlp arguments (no Qt)."""
from __future__ import annotations

# Browsers yt-dlp can read cookies from on Linux and Windows.
BROWSERS = ["firefox", "chrome", "chromium", "edge", "brave", "vivaldi", "opera", "whale"]

MODES = ("none", "app", "browser", "file")


def cookie_args(mode: str, browser: str = "", profile: str = "", path: str = "") -> list[str]:
    """mode: "none", "browser" (--cookies-from-browser), "file" (--cookies), or "app".

    "app" is the in-app YouTube sign-in: like "file", but ``path`` is the cookies file
    the sign-in window wrote (core/signin.py), not one the user picked.
    """
    if mode == "browser" and browser:
        profile = profile.strip()
        spec = f"{browser}:{profile}" if profile else browser
        return [f"--cookies-from-browser={spec}"]
    if mode in ("file", "app") and path.strip():
        return [f"--cookies={path.strip()}"]
    return []
