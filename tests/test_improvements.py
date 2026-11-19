"""Tests for job outcomes, logs, the saved queue, format picking and version checks (no Qt)."""
import io
import json
import os
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ytdlp_gui.core import queue_store  # noqa: E402
from ytdlp_gui.core.formats import AUDIO, BOTH, VIDEO, format_kind, format_string, pick_formats  # noqa: E402
from ytdlp_gui.core.runner import (DONE, DONE_WITH_ERRORS, FAILED, FINISHED, STOPPED, Job,  # noqa: E402
                                   clean_old_files, final_status, jobs_to_start)
from ytdlp_gui.core.version import (installed_version, is_outdated, latest_release,  # noqa: E402
                                    parse_version, pip_update_commands)


def run_job(script, prepare=None, log_path="", timeout=10):
    lines, progress = [], []
    job = Job([sys.executable, "-u", "-c", textwrap.dedent(script)], "t",
              on_line=lines.append, on_progress=progress.append)
    job.prepare = prepare
    job.log_path = log_path
    job.start()
    end = time.time() + timeout
    while job.status not in FINISHED and time.time() < end:
        time.sleep(0.05)
    return job, lines, progress


class FinalStatusTests(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(final_status(0, False, 0, 0), DONE)
        self.assertEqual(final_status(1, True, 5, 1), STOPPED)
        self.assertEqual(final_status(1, False, 0, 1), FAILED)        # nothing worked
        self.assertEqual(final_status(1, False, 3, 1), DONE_WITH_ERRORS)
        self.assertEqual(final_status(2, False, 3, 1), FAILED)        # bad options etc.
        self.assertEqual(final_status(1, False, 3, 0), FAILED)        # no error line seen

    def test_a_single_video_that_failed_half_way_is_a_failure(self):
        self.assertEqual(final_status(1, False, 1, 1, playlist=False), FAILED)


class JobOutcomeTests(unittest.TestCase):
    def test_channel_with_one_failed_video_is_done_with_errors(self):
        job, _lines, _ = run_job("""
            import sys
            print("[download] Downloading item 1 of 3")
            print("GUIPROG|finished|10|10|NA|NA|NA|NA|NA")
            print("[download] Downloading item 2 of 3")
            print("ERROR: [youtube] abc: Private video")
            print("[download] Downloading item 3 of 3")
            print("[download] x has already been recorded in the archive")
            sys.exit(1)
        """)
        self.assertEqual(job.status, DONE_WITH_ERRORS)
        self.assertEqual(job.errors, 1)
        self.assertEqual(job.completed, 2)
        self.assertEqual(job.last_error, "[youtube] abc: Private video")

    def test_failure_keeps_the_reason(self):
        job, *_ = run_job("""
            import sys
            print("ERROR: Unsupported URL: http://x")
            sys.exit(1)
        """)
        self.assertEqual(job.status, FAILED)
        self.assertEqual(job.last_error, "Unsupported URL: http://x")

    def test_counters_reset_on_retry(self):
        job, *_ = run_job('import sys; print("ERROR: boom"); sys.exit(1)')
        self.assertEqual(job.errors, 1)
        job.cmd = [sys.executable, "-c", "pass"]
        job.reset()
        job.start()
        for _ in range(100):
            if job.status in FINISHED:
                break
            time.sleep(0.05)
        self.assertEqual((job.status, job.errors, job.last_error), (DONE, 0, ""))

    def test_progress_is_throttled_but_finished_always_arrives(self):
        _job, _lines, progress = run_job("""
            for i in range(300):
                print(f"GUIPROG|downloading|{i}|300|NA|1|1|NA|NA")
            print("GUIPROG|finished|300|300|NA|NA|NA|NA|NA")
        """)
        self.assertLess(len(progress), 50)
        self.assertEqual(progress[0]["downloaded"], 0)
        self.assertEqual(progress[-1]["status"], "finished")

    def test_log_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "job.log")
            job, *_ = run_job('print("hello"); print("GUIPROG|downloading|1|2|NA|NA|NA|NA|NA")',
                              log_path=path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("hello\n", text)
            self.assertIn("exited with code 0", text)
            self.assertNotIn("GUIPROG", text)            # progress lines are not log noise

    def test_prepare_runs_before_every_start(self):
        calls = []

        def prepare(cmd):
            calls.append(list(cmd))
            return [sys.executable, "-c", "print('prepared')"]
        job, lines, _ = run_job("raise SystemExit(5)", prepare=prepare)
        self.assertEqual(job.status, DONE)
        self.assertIn("prepared", lines)
        self.assertEqual(len(calls), 1)

    def test_could_not_start_is_reported(self):
        job = Job(["/definitely/not/a/real/program"])
        job.start()
        for _ in range(100):
            if job.status in FINISHED:
                break
            time.sleep(0.05)
        self.assertEqual(job.status, FAILED)
        self.assertIn("could not start", job.last_error)

    def test_is_alive(self):
        job = Job([sys.executable, "-c", "import time; time.sleep(0.5)"])
        self.assertFalse(job.is_alive())
        job.start()
        self.assertTrue(job.is_alive())
        for _ in range(100):
            if job.status in FINISHED:
                break
            time.sleep(0.05)
        self.assertFalse(job.is_alive())


class FakeJob:
    def __init__(self, status="Queued", exclusive=False):
        self.status, self.exclusive = status, exclusive


class RetiredJobTests(unittest.TestCase):
    def test_a_removed_job_still_finishing_counts_toward_the_limit(self):
        retired = FakeJob("Running")          # removed from the list, still stopping
        queued = [FakeJob(), FakeJob()]
        self.assertEqual(jobs_to_start([retired, *queued], 2), [queued[0]])


class CleanOldFilesTests(unittest.TestCase):
    def test_only_old_files_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = os.path.join(tmp, "old.log"), os.path.join(tmp, "new.log")
            for p in (old, new):
                open(p, "w").close()
            os.utime(old, (0, 0))
            clean_old_files(tmp, 3600)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(new))

    def test_missing_folder_is_fine(self):
        clean_old_files("/no/such/folder/here", 1)


class QueueStoreTests(unittest.TestCase):
    def _job(self, status, **kw):
        job = Job(["python", "-c", "x", "--", "u"], "label")
        job.args = ["--", "u"]
        job.status = status
        for k, v in kw.items():
            setattr(job, k, v)
        return job

    def test_round_trip_skips_done_and_resumes_running(self):
        jobs = [self._job("Done"), self._job("Running"), self._job("Queued"),
                self._job("Failed", last_error="boom"), self._job("Stopped"),
                self._job("Running", resume=True, is_archive=True, exclusive=True,
                          url="https://c", folder="/f")]
        data = json.loads(json.dumps(queue_store.dump(jobs)))
        records = queue_store.load(data)
        self.assertEqual([r["status"] for r in records],
                         ["Queued", "Queued", "Failed", "Stopped", "Queued"])
        self.assertEqual(records[2]["last_error"], "boom")
        last = records[-1]
        self.assertEqual((last["archive"], last["exclusive"], last["url"], last["folder"]),
                         (True, True, "https://c", "/f"))
        self.assertEqual(last["args"], ["--", "u"])     # no launcher prefix is stored

    def test_bad_data_is_ignored(self):
        self.assertEqual(queue_store.load(None), [])
        self.assertEqual(queue_store.load({"version": 99, "jobs": [{"args": ["x"]}]}), [])
        bad = {"version": queue_store.VERSION,
               "jobs": [{"args": []}, {"args": "x"}, {"args": [1, 2]}, "nope",
                        {"args": ["--", "u"], "status": "Weird"}]}
        self.assertEqual([r["status"] for r in queue_store.load(bad)], ["Queued"])


class FormatPickTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(format_kind({"vcodec": "none", "acodec": "opus"}), AUDIO)
        self.assertEqual(format_kind({"vcodec": "avc1", "acodec": "none"}), VIDEO)
        self.assertEqual(format_kind({"vcodec": "avc1", "acodec": "mp4a"}), BOTH)
        self.assertEqual(format_kind({}), BOTH)

    def test_video_plus_audio(self):
        rows = pick_formats([("140", AUDIO), ("137", VIDEO)], "137")
        self.assertEqual(format_string(rows), "137+140")   # video first

    def test_newest_replaces_the_same_kind(self):
        rows = pick_formats([("251", AUDIO), ("137", VIDEO), ("140", AUDIO)], "140")
        self.assertEqual(format_string(rows), "137+140")

    def test_combined_format_stands_alone(self):
        self.assertEqual(format_string(pick_formats([("137", VIDEO), ("18", BOTH)], "18")), "18")
        self.assertEqual(format_string(pick_formats([("18", BOTH), ("137", VIDEO)], "137")), "137")

    def test_deselect_keeps_the_rest(self):
        self.assertEqual(format_string(pick_formats([("137", VIDEO)], None)), "137")
        self.assertEqual(pick_formats([], None), [])


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_version("2025.06.30"), (2025, 6, 30))
        self.assertEqual(parse_version("yt-dlp 2025.06.30.232839"), (2025, 6, 30, 232839))
        self.assertEqual(parse_version("garbage"), ())

    def test_outdated(self):
        self.assertTrue(is_outdated("2025.01.15", "2025.06.30"))
        self.assertFalse(is_outdated("2025.06.30", "2025.06.30"))
        self.assertFalse(is_outdated("2025.06.30.1234", "2025.06.30"))   # master build of it
        self.assertFalse(is_outdated("", "2025.06.30"))
        self.assertFalse(is_outdated("2025.06.30", ""))

    def test_installed_version_runs_the_given_yt_dlp(self):
        base = [sys.executable, "-c", "import sys; assert sys.argv[1] == '--version'; print('2025.06.30')"]
        self.assertEqual(installed_version(base), "2025.06.30")

    def test_installed_version_failure(self):
        with self.assertRaises(RuntimeError):
            installed_version([sys.executable, "-c", "import sys; sys.exit('broken')"])

    def test_latest_release_reads_the_tag(self):
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen["url"], seen["timeout"] = req.full_url, timeout
            return Resp(json.dumps({"tag_name": "2025.06.30"}).encode())
        self.assertEqual(latest_release(opener=opener), "2025.06.30")
        self.assertIn("api.github.com/repos/yt-dlp/yt-dlp/releases/latest", seen["url"])

    def test_pip_update_does_not_force_reinstall_dependencies(self):
        first, second = pip_update_commands("py", "SPEC")
        self.assertIn("--no-deps", first)
        self.assertIn("--force-reinstall", first)
        self.assertNotIn("--force-reinstall", second)
        self.assertEqual(first[-1], "SPEC")
        self.assertEqual(second[-1], "SPEC")


class ProjectMetadataTests(unittest.TestCase):
    def test_pyproject_pins_the_same_yt_dlp_as_requirements(self):
        from ytdlp_gui import __version__
        from ytdlp_gui.core.runner import YTDLP_SPEC
        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f'"{YTDLP_SPEC}"', text)
        self.assertIn('version = { attr = "ytdlp_gui.__version__" }', text)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


class QualityTests(unittest.TestCase):
    def setUp(self):
        from ytdlp_gui.core import quality
        self.q = quality

    def test_4k_caps_the_resolution(self):
        self.assertEqual(self.q.quality_args(self.q.FOUR_K, []), ["-S", "res:2160"])

    def test_best_adds_nothing_because_yt_dlp_already_prefers_resolution(self):
        self.assertEqual(self.q.quality_args(self.q.BEST, []), [])
        self.assertEqual(self.q.quality_args(self.q.BEST, ["-t", "mkv"]), [])

    def test_best_with_mp4_is_not_stuck_at_1080p(self):
        self.assertEqual(self.q.quality_args(self.q.BEST, ["-t", "mp4"]), ["-S", "res"])
        self.assertEqual(self.q.quality_args(self.q.FOUR_K, ["-t", "mp4"]), ["-S", "res:2160"])

    def test_bad_index_falls_back_to_best(self):
        self.assertEqual(self.q.quality_args(99, []), [])

    def test_max_height_ignores_audio(self):
        fmts = [{"height": 2160, "vcodec": "vp9"}, {"height": None, "vcodec": "none"},
                {"height": 1080, "vcodec": "avc1"}]
        self.assertEqual(self.q.max_height(fmts), 2160)
        self.assertEqual(self.q.max_height([]), 0)

    def test_notes(self):
        note = self.q.quality_note
        self.assertEqual(note(self.q.FOUR_K, [], True, True), "")
        self.assertIn("VP9 or AV1", note(self.q.FOUR_K, ["-t", "mp4"], True, True))
        self.assertIn("ffmpeg", note(self.q.FOUR_K, [], False, True))
        self.assertIn("deno", note(self.q.BEST, [], True, False))
        self.assertIn("only goes up to 1080p", note(self.q.FOUR_K, [], True, True, available=1080))
        only_hd = note(self.q.FOUR_K, ["-t", "mp4"], False, False, available=1080)
        self.assertIn("only goes up to 1080p", only_hd)
        self.assertNotIn("VP9", only_hd)                                # no 4K caveats for an HD video
        self.assertEqual(note(3, ["-t", "mp4"], False, False), "")       # 1080p: no 4K caveats
        self.assertEqual(note(self.q.FOUR_K, ["-t", "mp3"], False, False), "")


# A stand-in for yt-dlp on a long YouTube stream. Run number N (counted in a file) refuses
# fragment STOPS[N] with 403 the way yt-dlp reports it, then waits in its retry pause until
# interrupted. A run past the end of STOPS succeeds.
EXPIRING = """
import os, signal, sys, time
counter, stops, live = sys.argv[1], [int(x) for x in sys.argv[2].split(",") if x], sys.argv[3] == "live"
n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
open(counter, "w").write(str(n))
signal.signal(signal.SIGINT, signal.default_int_handler)
print("[dashsegments] Total fragments: unknown (live)" if live else "[dashsegments] Total fragments: 50", flush=True)
if n > len(stops):
    print("GUIPROG|finished|50|50|NA|NA|NA|NA|NA", flush=True)
    sys.exit(0)
print(f"[download] Got error: HTTP Error 403: Forbidden. Retrying fragment {stops[n-1]} (1/10)...", flush=True)
try:
    time.sleep(10)
    print(f"[download] fragment not found; Skipping fragment {stops[n-1]} ...", flush=True)
    sys.exit(0)
except KeyboardInterrupt:
    print("ERROR: Interrupted by user", flush=True)
    sys.exit(1)
"""


class ExpiredLinkTests(unittest.TestCase):
    def setUp(self):
        from ytdlp_gui.core import runner
        self.runner = runner
        self._delays = runner.REFRESH_DELAYS
        runner.REFRESH_DELAYS = (0.1,) * 4
        self.tmp = tempfile.TemporaryDirectory()
        self.counter = os.path.join(self.tmp.name, "runs")

    def tearDown(self):
        self.runner.REFRESH_DELAYS = self._delays
        self.tmp.cleanup()

    def _job(self, stops, live=False):
        lines = []
        job = Job([sys.executable, "-u", "-c", EXPIRING, self.counter, ",".join(map(str, stops)),
                   "live" if live else "vod"], "t", on_line=lines.append)
        return job, lines

    def _wait(self, job, timeout=20):
        end = time.time() + timeout
        while job.status not in FINISHED and time.time() < end:
            time.sleep(0.05)

    def _runs(self):
        return int(open(self.counter).read())

    def test_expired_links_are_refreshed_and_the_download_resumes(self):
        job, lines = self._job([100, 220])
        job.start()
        self._wait(job)
        self.assertEqual(job.status, DONE)
        self.assertEqual(self._runs(), 3)
        self.assertEqual(job.link_refreshes, 2)
        self.assertFalse(any("Skipping fragment" in ln for ln in lines))   # nothing was lost
        self.assertTrue(any("Getting fresh links" in ln for ln in lines))
        self.assertEqual(job.errors, 0)          # our own interrupts are not download errors

    def test_gives_up_when_fresh_links_get_no_further(self):
        job, lines = self._job([100, 100, 100, 100])
        job.start()
        self._wait(job)
        self.assertEqual(job.status, FAILED)
        self.assertEqual(self._runs(), 3)        # first try + 2 refreshes that got nowhere
        self.assertIn("kept refusing", job.last_error)
        self.assertIn("fragment 100", job.last_error)

    def test_refresh_budget_is_limited(self):
        job, _ = self._job([10, 20, 30, 40, 50, 60])
        job.start()
        self._wait(job)
        self.assertEqual(job.status, FAILED)
        self.assertEqual(job.link_refreshes, 4)

    def test_live_recordings_are_left_alone(self):
        job, lines = self._job([100], live=True)
        job.start()
        self._wait(job)
        self.assertEqual(self._runs(), 1)
        self.assertTrue(any("Skipping fragment" in ln for ln in lines))    # yt-dlp's own handling

    def test_can_be_turned_off(self):
        job, _ = self._job([100])
        job.auto_refresh = False
        job.start()
        self._wait(job)
        self.assertEqual(self._runs(), 1)

    def test_stop_during_the_wait_stops(self):
        self.runner.REFRESH_DELAYS = (30,)
        job, lines = self._job([100])
        job.start()
        end = time.time() + 10
        while not any("Getting fresh links" in ln for ln in lines) and time.time() < end:
            time.sleep(0.05)
        job.stop()
        self._wait(job, timeout=5)
        self.assertEqual(job.status, STOPPED)
        self.assertEqual(self._runs(), 1)


class UpgradeRunnerArgsTests(unittest.TestCase):
    def test_old_saved_jobs_get_the_current_runner_args(self):
        from ytdlp_gui.core.runner import PROGRESS_TEMPLATE, RUNNER_ARGS, upgrade_runner_args
        old = ["--newline", "--progress", "--color", "never", "--progress-template",
               PROGRESS_TEMPLATE, "-P", "/x", "--", "u"]
        self.assertEqual(upgrade_runner_args(old), [*RUNNER_ARGS, "-P", "/x", "--", "u"])
        current = [*RUNNER_ARGS, "--", "u"]
        self.assertEqual(upgrade_runner_args(current), current)
        self.assertEqual(upgrade_runner_args(["--", "u"]), ["--", "u"])
        self.assertIn("--retry-sleep", RUNNER_ARGS)


if __name__ == "__main__":
    unittest.main()
