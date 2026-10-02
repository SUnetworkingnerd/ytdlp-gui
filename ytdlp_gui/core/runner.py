"""Runs yt-dlp as a subprocess. No Qt imports, so it can be tested headless.

Callbacks (on_line / on_progress / on_state) are invoked from a worker thread;
the UI layer forwards them to Qt signals.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from collections import deque

PROGRESS_TAG = "GUIPROG"
_FIELDS = ("status", "downloaded_bytes", "total_bytes", "total_bytes_estimate",
           "speed", "eta", "fragment_index", "fragment_count")
PROGRESS_TEMPLATE = "download:" + "|".join(
    [PROGRESS_TAG] + [f"%(progress.{f})s" for f in _FIELDS])

# Arguments the runner always adds so output can be parsed line by line.
RUNNER_ARGS = ["--newline", "--progress", "--color", "never",
               "--progress-template", PROGRESS_TEMPLATE]

# When we run yt-dlp through the current Python interpreter we wrap it so that
# a stop request becomes KeyboardInterrupt on both platforms. That is how
# yt-dlp finalizes a live recording instead of dying mid-write. Windows sends
# CTRL_BREAK_EVENT (SIGBREAK), which Python does not turn into an interrupt by
# default, so it is mapped here.
LAUNCHER = (
    "import signal, sys\n"
    "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
    "if hasattr(signal, 'SIGBREAK'):\n"
    "    signal.signal(signal.SIGBREAK, signal.default_int_handler)\n"
    "import yt_dlp\n"
    "yt_dlp.main(sys.argv[1:])\n"
)

# yt-dlp is installed from the GitHub master branch (not PyPI). Keep this identical
# to the yt-dlp line in requirements.txt (a test checks that).
YTDLP_SPEC = "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz"

FORCE_KILL_AFTER = 20  # seconds between the stop request and terminate()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def tool_dirs() -> list[str]:
    """Extra places to look for ffmpeg, ffprobe and a JS runtime (packaged builds)."""
    if not is_frozen():
        return []
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    return [d for d in (exe_dir, os.path.join(exe_dir, "tools")) if os.path.isdir(d)]


def tool_path() -> str:
    return os.pathsep.join([*tool_dirs(), os.environ.get("PATH", "")])


def find_tool(name: str):
    return shutil.which(name, path=tool_path())


def base_command(binary: str = "") -> list[str]:
    """Command prefix that launches yt-dlp."""
    if binary:
        return [binary]
    if is_frozen():
        # The packaged app contains yt-dlp; run ourselves in embedded mode.
        return [sys.executable, "--ytdlp"]
    return [sys.executable, "-u", "-c", LAUNCHER]


def run_embedded(argv: list[str]) -> int:
    """Entry point for `ytdlp-gui --ytdlp ARGS...` (used by packaged builds)."""
    signal.signal(signal.SIGINT, signal.default_int_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    # Windowed executables can start with sys.stdout/stderr set to None.
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                stream = open(fd, "w", encoding="utf-8", buffering=1, closefd=False)
            except OSError:
                stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)
    import yt_dlp
    yt_dlp.main(argv)
    return 0


def _env() -> dict:
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PATH=tool_path())


def _popen_kwargs() -> dict:
    kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
              errors="replace", bufsize=1, env=_env())
    if os.name == "nt":
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.CREATE_NO_WINDOW)
    else:
        kw["start_new_session"] = True
    return kw


def hidden_run_kwargs() -> dict:
    """subprocess.run kwargs: tool dirs on PATH, and no console flash on Windows."""
    kw = {"env": _env()}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _num(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_progress(line: str):
    """Parse a GUIPROG line into a dict, or return None for ordinary log lines."""
    if not line.startswith(PROGRESS_TAG + "|"):
        return None
    parts = line.rstrip("\r\n").split("|")
    if len(parts) < 1 + len(_FIELDS):
        return None
    raw = dict(zip(_FIELDS, parts[1:]))
    downloaded = _num(raw["downloaded_bytes"])
    total = _num(raw["total_bytes"]) or _num(raw["total_bytes_estimate"])
    percent = 100.0 * downloaded / total if downloaded is not None and total else None
    return {
        "status": raw["status"],
        "downloaded": downloaded,
        "total": total,
        "percent": percent,          # None while the total size is unknown (live)
        "speed": _num(raw["speed"]),
        "eta": _num(raw["eta"]),
        "fragment": (_num(raw["fragment_index"]), _num(raw["fragment_count"])),
    }


def fmt_bytes(n) -> str:
    if not n:
        return ""
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_eta(seconds) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def display_command(args: list[str]) -> str:
    import shlex
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


class Job:
    """One yt-dlp process. Status: Queued, Running, Done, Failed, Stopped."""

    def __init__(self, cmd, label="", on_line=None, on_progress=None, on_state=None):
        self.cmd = cmd
        self.label = label
        self.on_line = on_line or (lambda s: None)
        self.on_progress = on_progress or (lambda p: None)
        self.on_state = on_state or (lambda s: None)
        self.status = "Queued"
        self.lock = threading.Lock()
        self._lines: deque = deque(maxlen=5000)
        self._proc = None
        self._stopping = False

    def log_text(self) -> str:
        with self.lock:
            return "\n".join(self._lines)

    def _emit_line(self, text: str) -> None:
        with self.lock:
            self._lines.append(text)
        self.on_line(text)

    def _set_status(self, status: str) -> None:
        self.status = status
        self.on_state(status)

    def start(self) -> None:
        if self.status != "Queued":
            return
        self._set_status("Running")
        threading.Thread(target=self._run, daemon=True).start()

    def reset(self) -> None:
        if self.status in ("Done", "Failed", "Stopped"):
            self._stopping = False
            self._proc = None
            self._set_status("Queued")

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(self.cmd, **_popen_kwargs())
        except OSError as exc:
            self._emit_line(f"[gui] could not start yt-dlp: {exc}")
            self._set_status("Failed")
            return
        for line in self._proc.stdout:
            progress = parse_progress(line)
            if progress is not None:
                self.on_progress(progress)
            else:
                self._emit_line(line.rstrip("\r\n"))
        self._proc.stdout.close()
        code = self._proc.wait()
        self._emit_line(f"[gui] yt-dlp exited with code {code}")
        if self._stopping:
            self._set_status("Stopped")
        else:
            self._set_status("Done" if code == 0 else "Failed")

    def stop(self) -> None:
        """Ask yt-dlp to stop gracefully (so live recordings get finalized)."""
        if self.status == "Queued":
            self._set_status("Stopped")
            return
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self._stopping = True
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt"
                             else signal.SIGINT)
        except OSError:
            pass
        timer = threading.Timer(FORCE_KILL_AFTER, self._force_kill)
        timer.daemon = True
        timer.start()

    def _force_kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._emit_line("[gui] did not stop in time, terminating")
            self._proc.terminate()
