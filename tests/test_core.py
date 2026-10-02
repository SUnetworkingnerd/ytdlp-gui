import optparse
import os
import sys
import textwrap
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ytdlp_gui.core.archive import (AUDIO_ONLY, ArchiveSettings, archive_name,  # noqa: E402
                                    build_archive, channel_urls)
from ytdlp_gui.core.cookies import BROWSERS, cookie_args  # noqa: E402
from ytdlp_gui.core.theme import effective, normalize  # noqa: E402
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


class ThemeTests(unittest.TestCase):
    def test_normalize_unknown_values(self):
        self.assertEqual(normalize("dark"), "dark")
        self.assertEqual(normalize("purple"), "system")
        self.assertEqual(normalize(None), "system")

    def test_system_follows_the_os(self):
        self.assertEqual(effective("system", True), "dark")
        self.assertEqual(effective("system", False), "light")

    def test_explicit_choice_wins(self):
        self.assertEqual(effective("dark", False), "dark")
        self.assertEqual(effective("light", True), "light")


class ArchiveTests(unittest.TestCase):
    CH = "https://www.youtube.com/@SomeChannel"

    def test_channel_root_covers_everything(self):
        self.assertEqual(channel_urls(self.CH + "/"), [self.CH])
        self.assertEqual(channel_urls(self.CH + "?si=abc"), [self.CH])

    def test_videos_only(self):
        self.assertEqual(channel_urls(self.CH, include_shorts_live=False), [self.CH + "/videos"])

    def test_quick_update_goes_tab_by_tab(self):
        self.assertEqual(channel_urls(self.CH, quick_update=True),
                         [self.CH + "/videos", self.CH + "/shorts", self.CH + "/streams"])
        self.assertEqual(channel_urls(self.CH, include_shorts_live=False, quick_update=True),
                         [self.CH + "/videos"])

    def test_other_urls_pass_through(self):
        for url in ("https://www.youtube.com/playlist?list=PL123", self.CH + "/shorts",
                    "https://www.twitch.tv/somebody/videos", "https://www.youtube.com/channel/UC123/videos"):
            self.assertEqual(channel_urls(url), [url])
        self.assertEqual(channel_urls("https://youtube.com/channel/UCabc123"),
                         ["https://youtube.com/channel/UCabc123"])

    def test_archive_name_is_per_channel_and_filesystem_safe(self):
        self.assertEqual(archive_name(self.CH), "SomeChannel.archive.txt")
        self.assertEqual(archive_name("https://www.youtube.com/channel/UCabc_123"), "UCabc_123.archive.txt")
        self.assertNotEqual(archive_name("https://www.youtube.com/@a"), archive_name("https://www.youtube.com/@b"))
        name = archive_name("https://example.com/some/weird path?x=1&y=2")
        self.assertNotRegex(name, r"[\\/:*?\"<>| ]")
        self.assertEqual(archive_name(""), "channel.archive.txt")

    def test_default_full_archive_command(self):
        args, urls = build_archive(ArchiveSettings(url=self.CH, folder="/data/yt"))
        self.assertEqual(urls, [self.CH])
        self.assertEqual(args[:2], ["-P", "/data/yt"])
        self.assertIn("--download-archive", args)
        self.assertEqual(args[args.index("--download-archive") + 1],
                         os.path.join("/data/yt", "SomeChannel.archive.txt"))
        for flag in ("--lazy-playlist", "--embed-metadata", "--write-info-json", "--write-thumbnail",
                     "--write-subs", "--sleep-interval", "--merge-output-format"):
            self.assertIn(flag, args)
        self.assertNotIn("--break-on-existing", args)
        self.assertEqual(args[args.index("--sub-langs") + 1], "all,-live_chat")

    def test_options_can_be_switched_off(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", metadata=False,
                                                subtitles=False, gentle=False))
        for flag in ("--embed-metadata", "--write-thumbnail", "--write-subs", "--sleep-interval",
                     "--sleep-subtitles"):
            self.assertNotIn(flag, args)

    def test_quality_and_audio_only(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=3))
        self.assertEqual(args[args.index("-S") + 1], "res:1080")
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=AUDIO_ONLY))
        self.assertIn("-x", args)
        self.assertNotIn("--merge-output-format", args)

    def test_quick_update_flags(self):
        args, urls = build_archive(ArchiveSettings(url=self.CH, folder="/d", quick_update=True))
        self.assertIn("--break-on-existing", args)
        self.assertIn("--break-per-input", args)
        self.assertEqual(len(urls), 3)

    def test_bad_quality_index_falls_back(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=99))
        self.assertNotIn("-S", args)

    def test_settings_round_trip_ignores_unknown_keys(self):
        s = ArchiveSettings(url="u", folder="f", quality=2, quick_update=True)
        self.assertEqual(ArchiveSettings.from_dict({**s.to_dict(), "future_option": 1}), s)


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
