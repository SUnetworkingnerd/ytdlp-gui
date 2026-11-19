import optparse
import os
import subprocess
import sys
import textwrap
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ytdlp_gui.core.archive import (AUDIO_FORMATS, AUDIO_ONLY, CONTAINERS, ArchiveSettings, archive_name,  # noqa: E402
                                    build_archive, channel_urls, pace_note, split_channel_urls, PACES)
from ytdlp_gui.core import signin  # noqa: E402
from ytdlp_gui.core.cookies import BROWSERS, cookie_args  # noqa: E402
from ytdlp_gui.core.theme import effective, normalize  # noqa: E402
from ytdlp_gui.core.options_model import OptionsModel, load_model, split_args  # noqa: E402
from ytdlp_gui.core.runner import (Job, PROGRESS_TEMPLATE, jobs_to_start, parse_progress,  # noqa: E402
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


class ModelCacheTests(unittest.TestCase):
    def test_round_trip_keeps_every_option(self):
        model = OptionsModel(fake_parser())
        again = OptionsModel.from_dict(model.to_dict())
        self.assertEqual(again.groups, model.groups)
        state = {it.key: 1 if it.kind == "pair" else "x"
                 for g in model.groups for it in g.items if it.kind in ("pair", "value")}
        self.assertEqual(again.build_args(state), model.build_args(state))

    def test_cache_is_used_without_importing_ytdlp_and_rebuilt_on_new_version(self):
        import json
        import tempfile
        from unittest import mock
        model = OptionsModel(fake_parser())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            with mock.patch("ytdlp_gui.core.options_model.OptionsModel", side_effect=[model]) as built:
                built.from_dict = OptionsModel.from_dict
                first = load_model(path, version="2026.1.1")        # nothing cached: builds + writes
                self.assertEqual(built.call_count, 1)
            self.assertTrue(os.path.isfile(path))
            with mock.patch("ytdlp_gui.core.options_model.OptionsModel") as never:
                never.from_dict = OptionsModel.from_dict
                again = load_model(path, version="2026.1.1")        # cache hit: no yt-dlp needed
                self.assertEqual(never.call_count, 0)
            self.assertEqual(again.groups, first.groups)
            with mock.patch("ytdlp_gui.core.options_model.OptionsModel", side_effect=[model]) as built:
                built.from_dict = OptionsModel.from_dict
                load_model(path, version="2026.2.2")                # yt-dlp updated: rebuilds
                self.assertEqual(built.call_count, 1)
            with open(path, encoding="utf-8") as fh:
                self.assertIn("2026.2.2", json.load(fh)["key"])

    def test_corrupt_cache_falls_back_to_building(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            with open(path, "w") as fh:
                fh.write("{not json")
            with mock.patch("ytdlp_gui.core.options_model.OptionsModel",
                            side_effect=[OptionsModel(fake_parser())]) as built:
                load_model(path, version="1")
                self.assertEqual(built.call_count, 1)


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

    def test_app_mode_uses_the_given_path(self):
        self.assertEqual(cookie_args("app", path="/data/youtube-cookies.txt"),
                         ["--cookies=/data/youtube-cookies.txt"])
        self.assertEqual(cookie_args("app", "firefox", "p"), [])

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

    def test_split_channel_urls(self):
        text = ("https://www.youtube.com/@a\n\n  # a comment\n"
                "https://www.youtube.com/@b/  \nhttps://www.youtube.com/@A\n"
                "https://www.youtube.com/@b\nhttps://example.com/x/ https://example.com/x\n")
        self.assertEqual(split_channel_urls(text), [
            "https://www.youtube.com/@a", "https://www.youtube.com/@b/", "https://example.com/x/"])
        self.assertEqual(split_channel_urls(""), [])
        self.assertEqual(split_channel_urls("  \n# only comments"), [])

    def test_each_channel_gets_its_own_archive_file(self):
        from dataclasses import replace
        base = ArchiveSettings(folder="/d")
        names = []
        for url in ("https://www.youtube.com/@a", "https://www.youtube.com/@b"):
            args, urls = build_archive(replace(base, url=url))
            names.append(args[args.index("--download-archive") + 1])
            self.assertEqual(urls, [url])
        self.assertEqual(len(set(names)), 2)

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
                                                subtitles=False, pace="normal"))
        for flag in ("--embed-metadata", "--write-thumbnail", "--write-subs", "--sleep-interval",
                     "--sleep-subtitles"):
            self.assertNotIn(flag, args)

    def test_quality_and_audio_only(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=3))
        self.assertEqual(args[args.index("-S") + 1], "res:1080")
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=AUDIO_ONLY))
        self.assertIn("-x", args)
        self.assertNotIn("--merge-output-format", args)

    def test_container_choice(self):
        for i, (_l, value) in enumerate(CONTAINERS):
            args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", container=i))
            if value:
                self.assertEqual(args[args.index("--merge-output-format") + 1], value)
            else:
                self.assertNotIn("--merge-output-format", args)

    def test_audio_format_choice(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=AUDIO_ONLY, audio_format=1))
        self.assertEqual(args[args.index("--audio-format") + 1], AUDIO_FORMATS[1][1])
        self.assertNotIn("--merge-output-format", args)

    def test_custom_format_replaces_quality(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=3,
                                                custom_format=" 137+140 "))
        self.assertEqual(args[args.index("-f") + 1], "137+140")
        self.assertNotIn("-S", args)
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=AUDIO_ONLY,
                                                custom_format="bv+ba"))
        self.assertNotIn("-x", args)

    def test_quick_update_flags(self):
        args, urls = build_archive(ArchiveSettings(url=self.CH, folder="/d", quick_update=True))
        self.assertIn("--break-on-existing", args)
        self.assertIn("--break-per-input", args)
        self.assertEqual(len(urls), 3)

    def test_bad_quality_index_falls_back(self):
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", quality=99))
        self.assertNotIn("-S", args)

    def test_cookies_are_opt_in_and_never_in_archive_args(self):
        self.assertFalse(ArchiveSettings().use_cookies)
        args, _ = build_archive(ArchiveSettings(url=self.CH, folder="/d", use_cookies=True))
        self.assertFalse([a for a in args if a.startswith("--cookies")])

    def test_settings_round_trip_ignores_unknown_keys(self):
        s = ArchiveSettings(url="u", folder="f", quality=2, quick_update=True)
        self.assertEqual(ArchiveSettings.from_dict({**s.to_dict(), "future_option": 1}), s)


class MaxSpeedTests(unittest.TestCase):
    def test_scales_with_cpus_within_limits(self):
        from ytdlp_gui.core.runner import max_speed_settings
        self.assertEqual(max_speed_settings(None), (4, 8))
        self.assertEqual(max_speed_settings(2), (4, 8))
        self.assertEqual(max_speed_settings(6), (6, 8))
        self.assertEqual(max_speed_settings(64), (8, 8))


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


def _cookie(name, domain=".youtube.com", value="v", **kw):
    return {"domain": domain, "path": "/", "name": name, "value": value, **kw}


class SignInTests(unittest.TestCase):
    NOW = 1_800_000_000

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "data", "youtube-cookies.txt")

    def rows(self, text):
        return [line.split("\t") for line in text.splitlines() if line and not line.startswith("# ")
                and line != "# Netscape HTTP Cookie File"]

    def test_netscape_format(self):
        text = signin.cookies_to_netscape(
            [_cookie("SAPISID", secure=True, expires=self.NOW + 100)], now=self.NOW)
        self.assertTrue(text.startswith("# Netscape HTTP Cookie File\n"))
        self.assertEqual(self.rows(text),
                         [[".youtube.com", "TRUE", "/", "TRUE", str(self.NOW + 100), "SAPISID", "v"]])

    def test_httponly_prefix_and_host_only_domain(self):
        rows = self.rows(signin.cookies_to_netscape(
            [_cookie("SID", domain="accounts.google.com", httponly=True, expires=self.NOW + 5)],
            now=self.NOW))
        self.assertEqual(rows[0][0], "#HttpOnly_accounts.google.com")
        self.assertEqual(rows[0][1], "FALSE")

    def test_session_cookies_get_a_future_expiry(self):
        for expires in (0, None):
            rows = self.rows(signin.cookies_to_netscape([_cookie("X", expires=expires)], now=self.NOW))
            self.assertGreater(int(rows[0][4]), self.NOW)

    def test_other_sites_are_dropped_and_duplicates_collapsed(self):
        text = signin.cookies_to_netscape(
            [_cookie("a", domain=".example.com"), _cookie("b", domain="notyoutube.com"),
             _cookie("c"), _cookie("c"), _cookie("d", domain=".google.com")], now=self.NOW)
        self.assertEqual([r[5] for r in self.rows(text)], ["c", "d"])

    def test_tabs_and_newlines_in_values_cannot_break_a_row(self):
        rows = self.rows(signin.cookies_to_netscape([_cookie("n", value="a\tb\nc")], now=self.NOW))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 7)

    def test_write_then_detect_sign_in(self):
        self.assertFalse(signin.is_signed_in(self.path, now=self.NOW))
        signin.write_cookie_file([_cookie("LOGIN_INFO", httponly=True, expires=self.NOW + 99)],
                                 self.path, now=self.NOW)
        self.assertTrue(signin.is_signed_in(self.path, now=self.NOW))
        if os.name != "nt":
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["youtube-cookies.txt"])

    def test_expired_or_unrelated_cookies_are_not_a_sign_in(self):
        signin.write_cookie_file([_cookie("SAPISID", expires=self.NOW - 1),
                                  _cookie("PREF", expires=self.NOW + 99)], self.path, now=self.NOW)
        self.assertFalse(signin.is_signed_in(self.path, now=self.NOW))

    def test_sign_out_removes_the_file_and_is_safe_twice(self):
        signin.write_cookie_file([_cookie("SID", expires=self.NOW + 9)], self.path, now=self.NOW)
        signin.sign_out(self.path)
        signin.sign_out(self.path)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(signin.is_signed_in(self.path))

    def test_every_process_gets_its_own_copy(self):
        signin.write_cookie_file([_cookie("SID", expires=self.NOW + 9)], self.path, now=self.NOW)
        args = ["-t", "mp4", f"--cookies={self.path}", "--proxy=x"]
        a, b = signin.isolate_args(args, self.path), signin.isolate_args(args, self.path)
        self.assertEqual(a[:2], ["-t", "mp4"])
        self.assertEqual(a[3], "--proxy=x")
        copy_a, copy_b = a[2][len("--cookies="):], b[2][len("--cookies="):]
        self.assertNotEqual(copy_a, copy_b)
        self.assertNotIn(self.path, (copy_a, copy_b))
        with open(self.path) as orig, open(copy_a) as dup:
            self.assertEqual(orig.read(), dup.read())

    def test_other_cookie_args_pass_through_untouched(self):
        for args in ([], ["--cookies-from-browser=firefox"], ["--cookies=/some/other.txt"]):
            self.assertEqual(signin.isolate_args(args, self.path), args)

    def test_missing_cookie_file_falls_back_to_the_original_args(self):
        args = [f"--cookies={self.path}"]
        self.assertEqual(signin.isolate_args(args, self.path), args)

    def test_stale_copies_are_cleaned_up(self):
        signin.write_cookie_file([_cookie("SID", expires=self.NOW + 9)], self.path, now=self.NOW)
        jobs = os.path.join(os.path.dirname(self.path), "jobs")
        os.makedirs(jobs)
        old = os.path.join(jobs, "old.txt")
        open(old, "w").close()
        os.utime(old, (self.NOW - 8 * 24 * 3600, self.NOW - 8 * 24 * 3600))
        signin.job_copy(self.path, now=self.NOW)
        self.assertFalse(os.path.exists(old))


class BrowserLoginTests(unittest.TestCase):
    NOW = 1_800_000_000

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "youtube-cookies.txt")
        self.profile = os.path.join(self.tmp.name, "profile")

    # ------------------------------------------------------------ finding browsers
    def test_finds_browsers_on_path_firefox_first(self):
        have = {"chromium": "/usr/bin/chromium", "firefox": "/usr/bin/firefox"}
        found = signin.find_login_browsers(which=have.get, windows=False)
        self.assertEqual([k for k, _l, _e in found], ["firefox", "chromium"])
        self.assertEqual(found[0], ("firefox", "Firefox", "/usr/bin/firefox"))

    def test_finds_nothing_when_no_browser_is_installed(self):
        self.assertEqual(signin.find_login_browsers(which=lambda n: None, windows=False), [])

    def test_windows_install_locations_are_checked_when_not_on_path(self):
        root = os.path.join("C:", "Program Files")
        want = os.path.join(root, r"Mozilla Firefox\firefox.exe")
        found = signin.find_login_browsers(which=lambda n: None, exists=lambda p: p == want,
                                           env={"PROGRAMFILES": root}, windows=True)
        self.assertEqual(found, [("firefox", "Firefox", want)])

    def test_windows_paths_are_ignored_elsewhere(self):
        found = signin.find_login_browsers(which=lambda n: None, exists=lambda p: True,
                                           env={"PROGRAMFILES": "/x"}, windows=False)
        self.assertEqual(found, [])

    # ------------------------------------------------------------ commands
    def test_firefox_command_uses_a_separate_profile(self):
        cmd = signin.browser_command("firefox", "/usr/bin/firefox", "/p")
        self.assertEqual(cmd[:4], ["/usr/bin/firefox", "--no-remote", "--profile", "/p"])
        self.assertEqual(cmd[-1], signin.LOGIN_URL)

    def test_chromium_family_command_uses_a_separate_user_data_dir(self):
        for key in ("chrome", "chromium", "brave", "edge", "vivaldi"):
            cmd = signin.browser_command(key, "/bin/x", "/p")
            self.assertIn("--user-data-dir=/p", cmd)
            self.assertEqual(cmd[-1], signin.LOGIN_URL)

    def test_login_page_is_googles_youtube_sign_in(self):
        self.assertTrue(signin.LOGIN_URL.startswith("https://accounts.google.com/"))
        self.assertIn("service=youtube", signin.LOGIN_URL)

    def test_export_arguments(self):
        args = signin.export_args("firefox", "/p", "/out.txt")
        self.assertEqual(args[args.index("--cookies-from-browser") + 1], "firefox:/p")
        self.assertEqual(args[args.index("--cookies") + 1], "/out.txt")
        self.assertIn("--skip-download", args)

    def test_profile_dir_is_not_hidden_on_linux(self):
        if os.name != "nt":
            self.assertFalse(os.path.basename(signin.login_profile_dir()).startswith("."))

    def test_reset_profile(self):
        os.makedirs(os.path.join(self.profile, "sub"))
        signin.reset_profile(self.profile)
        self.assertFalse(os.path.exists(self.profile))
        signin.reset_profile(self.profile, recreate=True)
        self.assertTrue(os.path.isdir(self.profile))

    # ------------------------------------------------------------ parsing
    def test_parse_netscape_round_trips(self):
        original = [_cookie("SID", domain=".google.com", httponly=True, secure=True,
                            expires=self.NOW + 5),
                    _cookie("PREF", domain="www.youtube.com", expires=self.NOW + 9, value="a=b")]
        parsed = signin.parse_netscape(signin.cookies_to_netscape(original, now=self.NOW))
        self.assertEqual(parsed, [{**c, "path": "/", "secure": bool(c.get("secure")),
                                   "httponly": bool(c.get("httponly"))} for c in original])

    def test_parse_netscape_skips_comments_blanks_and_bad_lines(self):
        text = "# Netscape HTTP Cookie File\n\n# note\nonly\ttwo\n.a.com\tTRUE\t/\tFALSE\t0\tn\tv\n"
        self.assertEqual([c["name"] for c in signin.parse_netscape(text)], ["n"])

    # ------------------------------------------------------------ export
    def fake_yt_dlp(self, cookies=None, stderr="", write=True, raises=None):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            if raises:
                raise raises
            if write:
                out = cmd[cmd.index("--cookies") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(signin.cookies_to_netscape(cookies or [], now=self.NOW))
            return subprocess.CompletedProcess(cmd, 0, "", stderr)
        run.calls = calls
        return run

    def export(self, run, **kw):
        return signin.export_login("firefox", self.profile, ["yt-dlp"], path=self.path,
                                   run=run, **kw)

    def test_export_keeps_only_youtube_and_google_cookies(self):
        run = self.fake_yt_dlp([
            _cookie("LOGIN_INFO", httponly=True, expires=self.NOW + 99999999999),
            _cookie("SID", domain=".google.com", expires=self.NOW + 99999999999),
            _cookie("tracker", domain=".example.com", expires=self.NOW + 99999999999)])
        ok, message = self.export(run)
        self.assertEqual((ok, message), (True, ""))
        with open(self.path) as fh:
            text = fh.read()
        self.assertIn("LOGIN_INFO", text)
        self.assertNotIn("tracker", text)
        self.assertTrue(signin.is_signed_in(self.path))

    def test_export_command_starts_with_the_given_yt_dlp(self):
        run = self.fake_yt_dlp([_cookie("SID", expires=self.NOW + 99999999999)])
        self.export(run)
        cmd = run.calls[0][0]
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertEqual(cmd[cmd.index("--cookies-from-browser") + 1], f"firefox:{self.profile}")

    def test_raw_export_is_always_deleted(self):
        run = self.fake_yt_dlp([_cookie("SID", expires=self.NOW + 99999999999)])
        self.export(run)
        raw = run.calls[0][0][run.calls[0][0].index("--cookies") + 1]
        self.assertFalse(os.path.exists(raw))
        self.assertFalse(os.path.exists(os.path.dirname(raw)))

    def test_not_signed_in_is_reported_and_nothing_is_kept_as_a_login(self):
        run = self.fake_yt_dlp([_cookie("PREF", expires=self.NOW + 99999999999)])
        ok, message = self.export(run)
        self.assertFalse(ok)
        self.assertIn("No YouTube login", message)
        self.assertFalse(signin.is_signed_in(self.path))

    def test_yt_dlp_failure_shows_its_error_output(self):
        run = self.fake_yt_dlp(write=False, stderr="ERROR: could not find firefox cookies database")
        ok, message = self.export(run)
        self.assertFalse(ok)
        self.assertIn("could not find firefox cookies database", message)

    def test_missing_yt_dlp_and_timeouts_are_reported(self):
        ok, message = self.export(self.fake_yt_dlp(raises=FileNotFoundError("no yt-dlp")))
        self.assertFalse(ok)
        self.assertIn("Could not run yt-dlp", message)
        ok, message = self.export(self.fake_yt_dlp(raises=subprocess.TimeoutExpired("x", 1)))
        self.assertFalse(ok)
        self.assertIn("too long", message)


class PaceTests(unittest.TestCase):
    CH = "https://www.youtube.com/@some"

    def args(self, **kw):
        return build_archive(ArchiveSettings(url=self.CH, folder="/d", **kw))[0]

    def sleeps(self, **kw):
        args = self.args(**kw)

        def val(flag):
            return args[args.index(flag) + 1] if flag in args else None
        return (val("--sleep-requests"), val("--sleep-interval"),
                val("--max-sleep-interval"), val("--sleep-subtitles"))

    def test_four_paces_and_gentle_is_the_default(self):
        self.assertEqual([k for _l, k in PACES], ["max", "normal", "gentle", "extra"])
        self.assertEqual(ArchiveSettings().pace, "gentle")
        self.assertEqual(self.sleeps(), ("1", "3", "8", "2"))

    def test_gentle_and_extra_gentle_pauses(self):
        self.assertEqual(self.sleeps(pace="gentle"), ("1", "3", "8", "2"))
        self.assertEqual(self.sleeps(pace="extra"), ("2", "10", "30", "5"))
        self.assertEqual(self.sleeps(pace="extra", subtitles=False), ("2", "10", "30", None))

    def test_normal_and_max_add_no_pauses(self):
        for pace in ("normal", "max"):
            self.assertEqual(self.sleeps(pace=pace), (None, None, None, None))

    def test_only_max_speed_adds_parallel_connections(self):
        for pace in ("normal", "gentle", "extra"):
            self.assertNotIn("--concurrent-fragments", self.args(pace=pace))
        args = self.args(pace="max")
        self.assertEqual(args[args.index("--concurrent-fragments") + 1], "8")

    def test_unknown_pace_falls_back_to_the_default(self):
        self.assertEqual(self.sleeps(pace="warp"), ("1", "3", "8", "2"))

    def test_only_extra_gentle_is_exclusive(self):
        self.assertEqual([ArchiveSettings(pace=k).exclusive for k in ("max", "normal", "gentle", "extra")],
                         [False, False, False, True])

    def test_settings_saved_by_earlier_versions_keep_their_meaning(self):
        self.assertEqual(ArchiveSettings.from_dict({"gentle": True}).pace, "gentle")
        self.assertEqual(ArchiveSettings.from_dict({"gentle": False}).pace, "normal")
        self.assertEqual(ArchiveSettings.from_dict({"gentle": True, "extra_gentle": True}).pace, "extra")
        self.assertEqual(ArchiveSettings.from_dict({"quality": 2}).pace, "gentle")
        self.assertEqual(ArchiveSettings.from_dict({"pace": "max", "gentle": True}).pace, "max")
        self.assertEqual(ArchiveSettings.from_dict({"pace": "bogus"}).pace, "gentle")

    def test_pace_round_trips_through_saved_settings(self):
        for _label, key in PACES:
            self.assertEqual(ArchiveSettings.from_dict(ArchiveSettings(pace=key).to_dict()).pace, key)

    def test_notes_describe_each_pace_and_warn_only_when_logged_in_at_speed(self):
        for _label, key in PACES:
            self.assertTrue(pace_note(key))
            self.assertNotIn("banned", pace_note(key, use_cookies=False))
        self.assertIn("banned", pace_note("max", use_cookies=True))
        self.assertIn("banned", pace_note("normal", use_cookies=True))
        self.assertNotIn("banned", pace_note("gentle", use_cookies=True))
        self.assertNotIn("banned", pace_note("extra", use_cookies=True))


class FakeJob:
    def __init__(self, status="Queued", exclusive=False):
        self.status, self.exclusive = status, exclusive


class JobSchedulingTests(unittest.TestCase):
    def test_new_jobs_are_not_exclusive(self):
        self.assertFalse(Job(["x"]).exclusive)

    def test_respects_parallel_limit(self):
        jobs = [FakeJob("Running"), FakeJob(), FakeJob(), FakeJob()]
        self.assertEqual(jobs_to_start(jobs, 2), [jobs[1]])
        self.assertEqual(jobs_to_start(jobs, 3), [jobs[1], jobs[2]])
        self.assertEqual(jobs_to_start(jobs, 1), [])

    def test_only_one_exclusive_job_runs_at_a_time(self):
        jobs = [FakeJob(exclusive=True), FakeJob(exclusive=True), FakeJob(exclusive=True)]
        self.assertEqual(jobs_to_start(jobs, 8), [jobs[0]])

    def test_waiting_exclusive_job_while_one_runs(self):
        jobs = [FakeJob("Running", True), FakeJob(exclusive=True)]
        self.assertEqual(jobs_to_start(jobs, 8), [])

    def test_waiting_exclusive_job_does_not_block_ordinary_downloads(self):
        jobs = [FakeJob("Running", True), FakeJob(exclusive=True), FakeJob(), FakeJob()]
        self.assertEqual(jobs_to_start(jobs, 2), [jobs[2]])
        self.assertEqual(jobs_to_start(jobs, 8), [jobs[2], jobs[3]])

    def test_finished_exclusive_job_frees_the_slot(self):
        jobs = [FakeJob("Done", True), FakeJob("Failed", True), FakeJob(exclusive=True),
                FakeJob(exclusive=True)]
        self.assertEqual(jobs_to_start(jobs, 8), [jobs[2]])

    def test_exclusive_jobs_still_count_toward_parallel(self):
        jobs = [FakeJob("Running", True), FakeJob("Running"), FakeJob()]
        self.assertEqual(jobs_to_start(jobs, 2), [])


if __name__ == "__main__":
    unittest.main()
