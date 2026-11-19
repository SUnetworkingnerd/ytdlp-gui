"""Runs yt-dlp as a subprocess. No Qt imports, so it can be tested headless.

Callbacks (on_line / on_progress / on_state) are invoked from a worker thread;
the UI layer forwards them to Qt signals.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

PROGRESS_TAG = "GUIPROG"
_FIELDS = ("status", "downloaded_bytes", "total_bytes", "total_bytes_estimate",
           "speed", "eta", "fragment_index", "fragment_count")
PROGRESS_TEMPLATE = "download:" + "|".join(
    [PROGRESS_TAG] + [f"%(progress.{f})s" for f in _FIELDS])

# Arguments the runner always adds so output can be parsed line by line.
RUNNER_ARGS = ["--newline", "--progress", "--color", "never",
               "--progress-template", PROGRESS_TEMPLATE,
               # Wait 1, 2, 4 ... 20 s between fragment retries (yt-dlp retries instantly by
               # default). This gives the expired-link check below time to step in before a
               # fragment is given up and skipped. An Options-tab --retry-sleep overrides it.
               "--retry-sleep", "fragment:exp=1:20"]

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

LOG_LINES = 2000       # per job, in memory; the full log is also written to disk (Job.log_path)
FORCE_KILL_AFTER = 20  # seconds between the stop request and terminate()


def upgrade_runner_args(args: list[str]) -> list[str]:
    """Swap the runner arguments a saved job starts with for the current ones, so jobs
    saved by an older version get newer runner settings (and "Copy command" still works)."""
    if args[:len(RUNNER_ARGS)] == RUNNER_ARGS:
        return list(args)
    head = args[:12]
    if not head or head[0] != "--newline" or PROGRESS_TEMPLATE not in head:
        return list(args)
    rest = args[head.index(PROGRESS_TEMPLATE) + 1:]
    if rest[:2] == ["--retry-sleep", "fragment:exp=1:20"]:
        rest = rest[2:]
    return [*RUNNER_ARGS, *rest]


def max_speed_settings(cpus) -> tuple[int, int]:
    """(downloads at once, fragments per download) for the "Max speed" button.

    Downloads are network bound, so this scales gently with the CPU count (ffmpeg merging
    is the CPU part) and stops at 8: past that, sites mostly just start throttling.
    """
    return min(8, max(4, cpus or 4)), 8


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
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PATH=tool_path())
    if os.name != "nt":
        # yt-dlp downloads with threads; glibc would otherwise keep one memory arena per thread.
        env.setdefault("MALLOC_ARENA_MAX", "2")
    return env


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


def jobs_to_start(jobs, parallel: int) -> list:
    """Queued jobs to start now, in queue order.

    At most ``parallel`` jobs run at once, and at most one exclusive job. A waiting
    exclusive job is skipped (not blocking the queue) so ordinary downloads still run.
    """
    running = [j for j in jobs if j.status == "Running"]
    exclusive_busy = any(getattr(j, "exclusive", False) for j in running)
    picked: list = []
    for job in jobs:
        if len(running) + len(picked) >= parallel:
            break
        if job.status != "Queued":
            continue
        if getattr(job, "exclusive", False):
            if exclusive_busy:
                continue
            exclusive_busy = True
        picked.append(job)
    return picked


# Final states. "Done with errors" means yt-dlp finished a playlist or channel but some
# items failed (yt-dlp skips a failed item, keeps going, and still exits with code 1).
DONE, DONE_WITH_ERRORS, FAILED, STOPPED = "Done", "Done with errors", "Failed", "Stopped"
FINISHED = (DONE, DONE_WITH_ERRORS, FAILED, STOPPED)

PROGRESS_INTERVAL = 0.25   # seconds; progress callbacks per job are throttled to this

# Lines that mean an item is already complete on disk / in the archive.
_ALREADY_DONE = re.compile(r"^\[download\] .* has already been (?:downloaded|recorded in the archive)")
# Fragmented downloads with a known length (not a live recording at the live edge).
_FINITE_FRAGMENTS = re.compile(r"^\[\w+\] Total fragments: \d+")
# First retry of a fragment refused with 403: on YouTube this means the download links
# expired (they are fetched once and live for a few hours, which long streams outlast).
_FRAGMENT_403 = re.compile(r"HTTP Error 403: Forbidden\. Retrying fragment (\d+) \(1/\d+\)")
# Seconds to wait before each fresh-links restart; its length is the most restarts per run.
REFRESH_DELAYS = (5, 10, 20, 30, 30, 30, 30, 30)
REFRESH_NO_PROGRESS = 2      # give up after this many restarts that got no further

_PLAYLIST = re.compile(r"^\[download\] (?:Downloading item \d+ of \d+|Downloading playlist: )")


def final_status(code: int, stopping: bool, completed: int, errors: int,
                 playlist: bool = True) -> str:
    """Status for a finished process. Only a playlist or channel can be "done with errors";
    a single video that failed half-way is a failure."""
    if stopping:
        return STOPPED
    if code == 0:
        return DONE
    if code == 1 and playlist and errors and completed:
        return DONE_WITH_ERRORS
    return FAILED


class Job:
    """One yt-dlp process. Status: Queued, Running, or one of FINISHED."""

    def __init__(self, cmd, label="", on_line=None, on_progress=None, on_state=None):
        self.cmd = cmd
        self.label = label
        self.id = uuid.uuid4().hex[:12]
        self.on_line = on_line or (lambda s: None)
        self.on_progress = on_progress or (lambda p: None)
        self.on_state = on_state or (lambda s: None)
        # Called with cmd right before each start; returns the command to run. Used to give
        # every run a fresh private copy of the cookies file (signin.isolate_args).
        self.prepare = None
        self.log_path = ""          # when set, every log line is also appended to this file
        self.status = "Queued"
        # Exclusive jobs (extra-gentle channel archives) never run alongside each other:
        # their pauses only slow one process, so several at once would defeat the point.
        self.exclusive = False
        # Restart with fresh links when a fragmented download's links expire (HTTP 403).
        self.auto_refresh = True
        self._wake = threading.Event()
        self.lock = threading.Lock()
        self._lines: deque = deque(maxlen=LOG_LINES)
        self._proc = None
        self._stopping = False
        self._reset_counters()

    def _reset_counters(self) -> None:
        self.errors = 0             # "ERROR:" lines seen in this run
        self.last_error = ""        # text of the most recent one, shown in the queue
        self.completed = 0          # items finished or already present
        self.playlist = False       # yt-dlp reported a playlist / channel
        self.link_refreshes = 0     # restarts with fresh links after expired-link 403s

    def log_text(self) -> str:
        with self.lock:
            return "\n".join(self._lines)

    def _emit_line(self, text: str) -> None:
        if text.startswith("ERROR: Interrupted by user"):
            pass                    # our own stop or link refresh, not a download error
        elif text.startswith("ERROR:"):
            self.errors += 1
            self.last_error = text[len("ERROR:"):].strip()
        elif _ALREADY_DONE.match(text):
            self.completed += 1
        elif _PLAYLIST.match(text):
            self.playlist = True
        with self.lock:
            self._lines.append(text)
            if self.log_path:
                try:
                    with open(self.log_path, "a", encoding="utf-8") as fh:
                        fh.write(text + "\n")
                except OSError:
                    pass
        self.on_line(text)

    def _set_status(self, status: str) -> None:
        self.status = status
        self.on_state(status)

    def start(self) -> None:
        if self.status != "Queued":
            return
        self._reset_counters()
        self._stopping = False
        self._wake.clear()
        self._set_status("Running")
        threading.Thread(target=self._run, daemon=True).start()

    def reset(self) -> None:
        if self.status in FINISHED:
            self._stopping = False
            self._proc = None
            self._set_status("Queued")

    def is_alive(self) -> bool:
        """True while the yt-dlp process exists (also while it is finalizing after a stop)."""
        proc = self._proc
        return self.status == "Running" or (proc is not None and proc.poll() is None)

    def _run(self) -> None:
        last_refresh_at = 0          # fragment number of the previous refresh
        stuck = 0
        while True:
            code, refresh_at = self._run_once()
            if code is None:
                return               # could not start; already reported
            if refresh_at is None or self._stopping:
                break
            # The links expired part-way. yt-dlp kept the finished fragments (.part + .ytdl),
            # so running it again fetches fresh links and resumes at that fragment.
            stuck = 0 if refresh_at > last_refresh_at else stuck + 1
            last_refresh_at = refresh_at
            if self.link_refreshes >= len(REFRESH_DELAYS) or stuck >= REFRESH_NO_PROGRESS:
                self.last_error = (f"YouTube kept refusing the download (HTTP 403) at fragment "
                                   f"{refresh_at}, even with fresh links. Try again later; Retry "
                                   "resumes where it stopped.")
                self._emit_line(f"[gui] {self.last_error}")
                self._set_status(FAILED)
                return
            delay = REFRESH_DELAYS[self.link_refreshes]
            self.link_refreshes += 1
            self._emit_line(
                f"[gui] Download links expired (HTTP 403 at fragment {refresh_at}). Getting fresh "
                f"links and resuming in {delay} s (refresh {self.link_refreshes} of "
                f"{len(REFRESH_DELAYS)}).")
            if self._wake.wait(delay) or self._stopping:
                break                # stopped while waiting
            self._emit_line("[gui] Resuming with fresh links.")
        self._set_status(final_status(code, self._stopping, self.completed, self.errors,
                                      self.playlist))

    def _run_once(self):
        """Run yt-dlp once. Returns (exit code or None if it could not start,
        fragment number if it was stopped because the links expired, else None)."""
        try:
            cmd = self.prepare(self.cmd) if self.prepare else self.cmd
            self._proc = proc = subprocess.Popen(cmd, **_popen_kwargs())
        except OSError as exc:
            self._emit_line(f"[gui] could not start yt-dlp: {exc}")
            self.last_error = f"could not start yt-dlp: {exc}"
            self._set_status(FAILED)
            return None, None
        last_progress = 0.0
        finite = False
        refresh_at = None
        for line in proc.stdout:
            progress = parse_progress(line)
            if progress is not None:
                if progress["status"] == "finished":
                    self.completed += 1
                now = time.monotonic()
                if progress["status"] != "downloading" or now - last_progress >= PROGRESS_INTERVAL:
                    last_progress = now
                    self.on_progress(progress)
                continue
            text = line.rstrip("\r\n")
            self._emit_line(text)
            if _FINITE_FRAGMENTS.match(text):
                finite = True
            elif finite and refresh_at is None and not self._stopping and self.auto_refresh:
                expired = _FRAGMENT_403.search(text)
                if expired:
                    # Stop now, during yt-dlp's retry pause, before the fragment is skipped.
                    refresh_at = int(expired.group(1))
                    self._interrupt(proc)
        proc.stdout.close()
        code = proc.wait()
        self._emit_line(f"[gui] yt-dlp exited with code {code}")
        return code, refresh_at

    @staticmethod
    def _interrupt(proc) -> None:
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        except OSError:
            pass

    def stop(self) -> None:
        """Ask yt-dlp to stop gracefully (so live recordings get finalized)."""
        if self.status == "Queued":
            self._set_status(STOPPED)
            return
        if self.status == "Running":
            self._stopping = True
            self._wake.set()         # also ends a wait between link refreshes
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self._interrupt(proc)
        timer = threading.Timer(FORCE_KILL_AFTER, self.kill)
        timer.daemon = True
        timer.start()

    def kill(self) -> None:
        """Terminate the process if it is still running (after a stop that took too long)."""
        if self._proc is not None and self._proc.poll() is None:
            self._emit_line("[gui] did not stop in time, terminating")
            self._proc.terminate()

    # Kept for callers of the old name.
    _force_kill = kill


def clean_old_files(folder: str, max_age: float, now: float | None = None) -> None:
    """Delete files in ``folder`` older than ``max_age`` seconds (used for job logs)."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        full = os.path.join(folder, name)
        try:
            if os.path.isfile(full) and now - os.path.getmtime(full) > max_age:
                os.remove(full)
        except OSError:
            pass
