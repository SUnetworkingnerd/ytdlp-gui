"""Support for the in-app "Sign in to YouTube" button (no Qt, so it can be tested headless).

Google refuses sign-in from embedded browsers, so the sign-in uses a real browser:

1. A real, installed browser opens on a fresh, separate profile (it never touches your
   normal browser profile) and the user signs in to Google there as usual.
2. When that browser is closed, yt-dlp reads the new profile's cookies once and the app
   keeps only the YouTube/Google ones in its own cookies file (``--cookies``).
3. The temporary profile is deleted. Nothing else ever reopens that session, so YouTube
   has no reason to rotate the cookies, which is what yt-dlp recommends to avoid
   "cookies are no longer valid" errors.
"""
from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import tempfile
import time

COOKIE_FILE_NAME = "youtube-cookies.txt"

# Only these sites matter to yt-dlp's YouTube support; everything else is dropped so the
# file holds as little as possible.
_KEEP_DOMAIN = re.compile(r"(^|\.)(youtube|google)\.com$")

# Cookies that exist only while someone is signed in to Google / YouTube.
AUTH_COOKIES = {"SID", "__Secure-3PSID", "SAPISID", "__Secure-3PAPISID", "LOGIN_INFO"}

SESSION_COOKIE_LIFETIME = 365 * 24 * 3600   # session cookies get a long expiry in the file
STALE_COPY_AGE = 7 * 24 * 3600              # per-job copies older than this are deleted

_copy_counter = itertools.count(1)


def data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "ytdlp-gui")


def cookies_file() -> str:
    return os.path.join(data_dir(), COOKIE_FILE_NAME)


def _is_auth_domain(domain: str) -> bool:
    return bool(_KEEP_DOMAIN.search(domain.lstrip(".").lower()))


def _field(text: str) -> str:
    """Cookie fields are tab separated, so tabs and newlines must never appear inside one."""
    return str(text).replace("\t", " ").replace("\r", "").replace("\n", "")


def cookies_to_netscape(cookies: list[dict], now: float | None = None) -> str:
    """Render cookie dicts as a Netscape cookies.txt (the format yt-dlp's --cookies reads).

    Each dict needs domain, path, name, value and may have secure, httponly and expires
    (epoch seconds; 0 or None means a session cookie). Cookies from sites other than
    youtube.com and google.com are skipped.
    """
    now = time.time() if now is None else now
    lines = [
        "# Netscape HTTP Cookie File",
        "# Written by yt-dlp GUI after signing in to YouTube. It gives access to the",
        "# account, so never share it.",
    ]
    seen = set()
    for c in cookies:
        domain = str(c.get("domain", ""))
        if not domain or not _is_auth_domain(domain):
            continue
        key = (domain, c.get("path") or "/", c.get("name"))
        if key in seen:
            continue
        seen.add(key)
        expires = c.get("expires") or 0
        if expires <= 0:
            expires = int(now + SESSION_COOKIE_LIFETIME)
        prefix = "#HttpOnly_" if c.get("httponly") else ""
        lines.append("\t".join([
            prefix + _field(domain),
            "TRUE" if domain.startswith(".") else "FALSE",
            _field(c.get("path") or "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(int(expires)),
            _field(c.get("name", "")),
            _field(c.get("value", "")),
        ]))
    return "\n".join(lines) + "\n"


def write_cookie_file(cookies: list[dict], path: str | None = None, now: float | None = None) -> str:
    """Atomically write the cookies file (owner-only permissions on Linux) and return its path."""
    path = path or cookies_file()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    text = cookies_to_netscape(cookies, now)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def is_signed_in(path: str | None = None, now: float | None = None) -> bool:
    """True if the cookies file holds an unexpired Google/YouTube sign-in cookie."""
    path = path or cookies_file()
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return False
    for line in lines:
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7 or parts[5] not in AUTH_COOKIES or not parts[6]:
            continue
        try:
            expires = int(parts[4])
        except ValueError:
            continue
        if expires == 0 or expires > now:
            return True
    return False


# ---------------------------------------------------------------------------- real-browser login
LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube"
             "&continue=https%3A%2F%2Fwww.youtube.com%2F")
EXPORT_URL = "https://www.youtube.com/robots.txt"   # any tiny page; the point is the cookie export

# (yt-dlp browser name, label, executable names on PATH). Firefox is first: its cookie
# file is not encrypted, so yt-dlp can read it on every system. Chromium-based browsers
# encrypt cookies with a key that is not always available (notably Chrome on Windows).
LOGIN_BROWSERS = [
    ("firefox", "Firefox", ["firefox"]),
    ("chrome", "Google Chrome", ["google-chrome", "google-chrome-stable", "chrome"]),
    ("chromium", "Chromium", ["chromium", "chromium-browser"]),
    ("brave", "Brave", ["brave-browser", "brave"]),
    ("edge", "Microsoft Edge", ["microsoft-edge", "microsoft-edge-stable", "msedge"]),
    ("vivaldi", "Vivaldi", ["vivaldi", "vivaldi-stable"]),
]

# Usual install locations on Windows, where browsers are rarely on PATH.
_WINDOWS_PATHS = {
    "firefox": [r"Mozilla Firefox\firefox.exe"],
    "chrome": [r"Google\Chrome\Application\chrome.exe"],
    "brave": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
    "edge": [r"Microsoft\Edge\Application\msedge.exe"],
    "vivaldi": [r"Vivaldi\Application\vivaldi.exe"],
}


def find_login_browsers(which=shutil.which, exists=os.path.exists, env=None,
                        windows: bool | None = None) -> list[tuple[str, str, str]]:
    """Installed browsers that can do the sign-in, as (yt-dlp name, label, executable)."""
    env = os.environ if env is None else env
    windows = (os.name == "nt") if windows is None else windows
    roots = [env.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if env.get(k)]
    found = []
    for key, label, names in LOGIN_BROWSERS:
        exe = next((p for p in (which(n) for n in names) if p), None)
        if not exe and windows:
            exe = next((full for rel in _WINDOWS_PATHS.get(key, []) for root in roots
                        for full in [os.path.join(root, rel)] if exists(full)), None)
        if exe:
            found.append((key, label, exe))
    return found


def login_profile_dir() -> str:
    """Where the temporary browser profile lives.

    On Linux this is a visible folder in the home directory, not under ~/.local/share:
    Snap and Flatpak browsers are not allowed to use hidden folders, and would refuse the profile.
    """
    if os.name == "nt":
        return os.path.join(data_dir(), "login-profile")
    return os.path.join(os.path.expanduser("~"), "ytdlp-gui-login")


def reset_profile(path: str | None = None, recreate: bool = False) -> None:
    path = path or login_profile_dir()
    shutil.rmtree(path, ignore_errors=True)
    if recreate:
        os.makedirs(path, exist_ok=True)


def browser_command(key: str, exe: str, profile_dir: str, url: str = LOGIN_URL) -> list[str]:
    """Command that opens ``url`` in a new, separate browser profile at ``profile_dir``."""
    if key == "firefox":
        return [exe, "--no-remote", "--profile", profile_dir, url]
    return [exe, f"--user-data-dir={profile_dir}", "--no-first-run",
            "--no-default-browser-check", url]


def export_args(key: str, profile_dir: str, out_path: str) -> list[str]:
    """yt-dlp arguments that read the profile's cookies and save them to ``out_path``."""
    return ["--cookies-from-browser", f"{key}:{profile_dir}", "--cookies", out_path,
            "--skip-download", "--no-warnings", "--no-playlist", EXPORT_URL]


def parse_netscape(text: str) -> list[dict]:
    """Read a Netscape cookies.txt into the dicts that ``cookies_to_netscape`` takes."""
    cookies = []
    for line in text.splitlines():
        httponly = line.startswith("#HttpOnly_")
        if httponly:
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        try:
            expires = int(parts[4])
        except ValueError:
            expires = 0
        cookies.append({"domain": parts[0], "path": parts[2], "secure": parts[3] == "TRUE",
                        "expires": expires, "name": parts[5], "value": parts[6],
                        "httponly": httponly})
    return cookies


def export_login(key: str, profile_dir: str, ytdlp_cmd: list[str], path: str | None = None,
                 run=subprocess.run, run_kwargs: dict | None = None,
                 timeout: int = 120) -> tuple[bool, str]:
    """Read the sign-in out of the browser profile with yt-dlp and save it.

    Returns (ok, message). The raw export holds every cookie in the profile, so it goes
    to a temporary folder, only the YouTube/Google cookies are kept, and it is deleted.
    """
    path = path or cookies_file()
    folder = tempfile.mkdtemp(prefix="ytdlp-gui-export-")
    raw = os.path.join(folder, "cookies.txt")        # must not exist yet, yt-dlp creates it
    try:
        cmd = [*ytdlp_cmd, *export_args(key, profile_dir, raw)]
        try:
            proc = run(cmd, capture_output=True, text=True, timeout=timeout, **(run_kwargs or {}))
        except subprocess.TimeoutExpired:
            return False, "Reading the browser's cookies took too long."
        except OSError as exc:
            return False, f"Could not run yt-dlp: {exc}"
        try:
            with open(raw, encoding="utf-8", errors="replace") as fh:
                cookies = parse_netscape(fh.read())
        except OSError:
            detail = (proc.stderr or proc.stdout or "").strip()[-400:]
            return False, "yt-dlp could not read the browser's cookies." + (f"\n\n{detail}" if detail else "")
        try:
            write_cookie_file(cookies, path)
        except OSError as exc:
            return False, f"Could not save the login: {exc}"
        if not is_signed_in(path):
            return False, ("No YouTube login was found in the browser. Make sure you finish signing "
                           "in (until YouTube's home page shows), and then close the browser.")
        return True, ""
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def sign_out(path: str | None = None) -> None:
    try:
        os.unlink(path or cookies_file())
    except FileNotFoundError:
        pass


def _clean_stale_copies(folder: str, now: float) -> None:
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        full = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(full) > STALE_COPY_AGE:
                os.unlink(full)
        except OSError:
            pass


def job_copy(path: str | None = None, now: float | None = None) -> str:
    """A private copy of the cookies file for one yt-dlp process.

    yt-dlp rewrites its --cookies file when it exits. With several downloads running at
    once they would overwrite each other, so every process gets its own copy and the
    signed-in original is never touched.
    """
    path = path or cookies_file()
    now = time.time() if now is None else now
    folder = os.path.join(os.path.dirname(path), "jobs")
    os.makedirs(folder, exist_ok=True)
    _clean_stale_copies(folder, now)
    copy = os.path.join(folder, f"cookies-{os.getpid()}-{next(_copy_counter)}.txt")
    shutil.copyfile(path, copy)
    if os.name != "nt":
        os.chmod(copy, 0o600)
    return copy


def isolate_args(args: list[str], path: str | None = None) -> list[str]:
    """Swap ``--cookies=<signed-in file>`` for a private per-process copy; other args pass through."""
    master = f"--cookies={path or cookies_file()}"
    if master not in args:
        return list(args)
    try:
        copy = job_copy(path)
    except OSError:
        return list(args)          # fall back to the shared file rather than failing the download
    return [f"--cookies={copy}" if a == master else a for a in args]
