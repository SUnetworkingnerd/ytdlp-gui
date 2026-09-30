import optparse
import os
import sys
import textwrap
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ytdlp_gui.core.cookies import BROWSERS, cookie_args  # noqa: E402
from ytdlp_gui.core.options_model import OptionsModel, split_args  # noqa: E402
from ytdlp_gui.core.runner import (Job, PROGRESS_TEMPLATE, parse_progress,  # noqa: E402
                                   fmt_bytes, fmt_eta)


def fake_parser():
    """Mimics the shapes yt-dlp's real parser uses."""
    p = optparse.OptionParser(add_help_option=False)
    g = optparse.OptionGroup(p, "General Options")
    g.add_option("--live-from-start", action="store_true", dest="lfs", help="live from start")
    g.add_option("--no-live-from-start", action="store_false", dest="lfs", help="from now")
    g.add_option("--proxy", dest="proxy", metavar="URL", help="proxy")
    g.add_option("-I", "--playlist-items", dest="items", metavar="ITEM_SPEC", help="items")
    g.add_option("--add-headers", action="append", dest="headers", metavar="FIELD:VALUE", help="hdr")
    g.add_option("--audio-format", type="choice", choices=["mp3", "opus"], dest="af", help="fmt")
    g.add_option("--replace-in-metadata", nargs=3, dest="rim", metavar="F R X", help="rep")
    g.add_option("--old-thing", dest="old", help=optparse.SUPPRESS_HELP)
    g.add_option("--paths", "-P", action="append", dest="paths", help="handled elsewhere")
    g.add_option("--cookies", dest="cookies", metavar="FILE", help="cookie file")
    g.add_option("--cookies-from-browser", dest="cfb", metavar="BROWSER", help="browser cookies")
    g.add_option("--abort-on-error", action="store_true", dest="abort", help="abort")
    g.add_option("--no-abort-on-error", action="store_false", dest="abort", help="continue")
    p.add_option_group(g)
    return p


class OptionsModelTests(unittest.TestCase):
    def setUp(self):
        self.model = OptionsModel(fake_parser())
        self.items = {i.key: i for g in self.model.groups for i in g.items}

    def test_hidden_and_suppressed_options_are_dropped(self):
        self.assertNotIn("--old-thing", self.items)
        self.assertNotIn("--paths", self.items)
        self.assertNotIn("--no-live-from-start", self.items)   # folded/hidden

    def test_cookie_options_are_owned_by_the_download_tab(self):
        self.assertNotIn("--cookies", self.items)
        self.assertNotIn("--cookies-from-browser", self.items)

    def test_no_pairs_become_three_state(self):
        self.assertEqual(self.items["--abort-on-error"].kind, "pair")
        self.assertNotIn("--no-abort-on-error", self.items)

    def test_kinds(self):
        self.assertEqual(self.items["--proxy"].kind, "value")
        self.assertEqual(self.items["--add-headers"].kind, "multi")
        self.assertEqual(self.items["--audio-format"].kind, "choice")
        self.assertEqual(self.items["--audio-format"].choices, ["mp3", "opus"])

    def test_build_args(self):
        state = {
            "--proxy": "socks5://127.0.0.1:1080",
            "--playlist-items": "-5::2",                 # leading dash must survive
            "--add-headers": "A:b\n\nC:d",
            "--audio-format": "mp3",
            "--replace-in-metadata": 'title "a b" x',
            "--abort-on-error": 2,
        }
        self.assertEqual(self.model.build_args(state), [
            "--proxy=socks5://127.0.0.1:1080",
            "--playlist-items=-5::2",
            "--add-headers=A:b", "--add-headers=C:d",
            "--audio-format=mp3",
            "--replace-in-metadata", "title", "a b", "x",
            "--no-abort-on-error",
        ])

    def test_only_filter(self):
        state = {"--proxy": "x", "--audio-format": "mp3"}
        self.assertEqual(self.model.build_args(state, only={"--proxy"}), ["--proxy=x"])

    def test_split_args_quotes(self):
        self.assertEqual(split_args('a "b c" d'), ["a", "b c", "d"])


class CookieTests(unittest.TestCase):
    def test_none_adds_nothing(self):
        self.assertEqual(cookie_args("none", "firefox", "p", "/x.txt"), [])

    def test_browser(self):
        self.assertEqual(cookie_args("browser", "firefox"), ["--cookies-from-browser=firefox"])
        self.assertEqual(cookie_args("browser", "chrome", " Profile 2 "),
                         ["--cookies-from-browser=chrome:Profile 2"])
        self.assertEqual(cookie_args("browser", "chrome", r"C:\Users\me\prof"),
                         [r"--cookies-from-browser=chrome:C:\Users\me\prof"])

    def test_file(self):
        self.assertEqual(cookie_args("file", path=" /tmp/c.txt "), ["--cookies=/tmp/c.txt"])
        self.assertEqual(cookie_args("file", path="  "), [])

    def test_browser_list_matches_yt_dlp_readme(self):
        for name in ("firefox", "chrome", "edge", "brave"):
            self.assertIn(name, BROWSERS)


class RunnerTests(unittest.TestCase):
    def test_progress_template_shape(self):
        self.assertTrue(PROGRESS_TEMPLATE.startswith("download:GUIPROG|%(progress.status)s"))

    def test_parse_progress_known_total(self):
        p = parse_progress("GUIPROG|downloading|500|1000|NA|2048.5|30|NA|NA\n")
        self.assertEqual(p["percent"], 50.0)
        self.assertEqual(p["speed"], 2048.5)
        self.assertEqual(p["eta"], 30.0)

    def test_parse_progress_live_unknown_total(self):
        p = parse_progress("GUIPROG|downloading|4096|NA|NA|100|NA|3|NA")
        self.assertIsNone(p["percent"])
        self.assertEqual(p["downloaded"], 4096.0)

    def test_parse_progress_ignores_normal_lines(self):
        self.assertIsNone(parse_progress("[download] Destination: x.mp4"))

    def test_formatters(self):
        self.assertEqual(fmt_bytes(1536), "1.5 KiB")
        self.assertEqual(fmt_eta(3725), "1:02:05")
        self.assertEqual(fmt_eta(65), "1:05")

    def _run_job(self, script, stop_after=None):
        lines, progress, states = [], [], []
        job = Job([sys.executable, "-u", "-c", textwrap.dedent(script)], "t",
                  on_line=lines.append, on_progress=progress.append, on_state=states.append)
        job.start()
        if stop_after is not None:
            time.sleep(stop_after)
            job.stop()
        for _ in range(100):
            if job.status in ("Done", "Failed", "Stopped"):
                break
            time.sleep(0.1)
        return job, lines, progress, states

    def test_job_success_and_progress(self):
        job, lines, progress, _ = self._run_job("""
            print("hello")
            print("GUIPROG|downloading|10|100|NA|5|9|NA|NA")
        """)
        self.assertEqual(job.status, "Done")
        self.assertIn("hello", lines)
        self.assertEqual(progress[0]["percent"], 10.0)

    def test_job_failure(self):
        job, *_ = self._run_job("import sys; sys.exit(3)")
        self.assertEqual(job.status, "Failed")

    @unittest.skipIf(os.name == "nt", "POSIX SIGINT path")
    def test_graceful_stop_sends_interrupt(self):
        job, lines, _, _ = self._run_job("""
            import signal, time, sys
            signal.signal(signal.SIGINT, signal.default_int_handler)
            print("recording", flush=True)
            try:
                while True: time.sleep(0.1)
            except KeyboardInterrupt:
                print("finalizing", flush=True)
                sys.exit(1)
        """, stop_after=1.0)
        self.assertEqual(job.status, "Stopped")
        self.assertIn("finalizing", lines)

    def test_stop_queued_job(self):
        job = Job(["true"])
        job.stop()
        self.assertEqual(job.status, "Stopped")
        job.reset()
        self.assertEqual(job.status, "Queued")


if __name__ == "__main__":
    unittest.main()
