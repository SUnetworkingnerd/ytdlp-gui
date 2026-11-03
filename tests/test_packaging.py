import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytdlp_gui.core import runner  # noqa: E402


def load_build_module():
    spec = importlib.util.spec_from_file_location("build_windows", ROOT / "packaging" / "build_windows.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SourceTests(unittest.TestCase):
    def test_ytdlp_comes_from_github_not_pypi(self):
        lines = (ROOT / "requirements.txt").read_text().splitlines()
        self.assertIn(runner.YTDLP_SPEC, lines)
        self.assertIn("github.com/yt-dlp/yt-dlp/archive/master", runner.YTDLP_SPEC)
        # no plain PyPI requirement for yt-dlp alongside it
        self.assertEqual([ln for ln in lines if ln.startswith("yt-dlp")], [runner.YTDLP_SPEC])

    def test_icons_exist(self):
        for name in ("icon.ico", "icon.png", "logo.png", "icons/256x256.png"):
            self.assertTrue((ROOT / "ytdlp_gui" / "assets" / name).exists(), name)


class FrozenModeTests(unittest.TestCase):
    def setUp(self):
        self._frozen = getattr(sys, "frozen", None)
        self._exe = sys.executable
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        if self._frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self._frozen
        sys.executable = self._exe
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_frozen_base_command_uses_embedded_mode(self):
        sys.frozen = True
        sys.executable = str(self.tmp / "ytdlp-gui.exe")
        self.assertEqual(runner.base_command(), [sys.executable, "--ytdlp"])
        self.assertEqual(runner.base_command("/x/yt-dlp"), ["/x/yt-dlp"])

    def test_not_frozen_uses_python_launcher(self):
        cmd = runner.base_command()
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[-1], runner.LAUNCHER)

    def test_tools_folder_is_searched_when_frozen(self):
        tools = self.tmp / "tools"
        tools.mkdir()
        name = "fake-ffmpeg-tool"
        exe = tools / (name + (".exe" if os.name == "nt" else ""))
        exe.write_text("x")
        exe.chmod(0o755)
        self.assertIsNone(runner.find_tool(name))            # not frozen: not searched
        sys.frozen = True
        sys.executable = str(self.tmp / "ytdlp-gui.exe")
        self.assertIsNotNone(runner.find_tool(name))
        self.assertIn(str(tools), runner.hidden_run_kwargs()["env"]["PATH"])


class BuildScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.build = load_build_module()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_download_tool_extracts_wanted_files_from_nested_zip(self):
        archive = self.tmp / "ffmpeg.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("ffmpeg-master/bin/ffmpeg.exe", "A")
            z.writestr("ffmpeg-master/bin/ffprobe.exe", "B")
            z.writestr("ffmpeg-master/bin/ffplay.exe", "C")
        dest = self.tmp / "tools"
        self.build.download_tool(archive.as_uri(), {"ffmpeg.exe", "ffprobe.exe"}, dest)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["ffmpeg.exe", "ffprobe.exe"])

    def test_download_tool_reports_missing_member(self):
        archive = self.tmp / "x.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("readme.txt", "hi")
        with self.assertRaises(RuntimeError):
            self.build.download_tool(archive.as_uri(), {"deno.exe"}, self.tmp / "t")

    def test_make_zip_onedir_and_onefile(self):
        self.build.ROOT = self.tmp
        (self.tmp / "dist").mkdir()
        one = self.tmp / "dist" / "ytdlp-gui"
        (one / "tools").mkdir(parents=True)
        (one / "ytdlp-gui.exe").write_text("e")
        (one / "tools" / "deno.exe").write_text("d")
        z = self.build.make_zip(one, onefile=False)
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(sorted(zf.namelist()), ["ytdlp-gui/tools/deno.exe", "ytdlp-gui/ytdlp-gui.exe"])
        flat = self.tmp / "dist2"
        (flat / "tools").mkdir(parents=True)
        (flat / "ytdlp-gui.exe").write_text("e")
        (flat / "tools" / "deno.exe").write_text("d")
        z = self.build.make_zip(flat, onefile=True)
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(sorted(zf.namelist()), ["tools/deno.exe", "ytdlp-gui.exe"])


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "Linux installer")
class LinuxInstallerTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, HOME=str(self.home), YTDLP_GUI_SKIP_PIP="1")
        self.env.pop("XDG_DATA_HOME", None)
        self.script = str(ROOT / "packaging" / "install_linux.sh")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_script(self, *args):
        return subprocess.run(["bash", self.script, *args], env=self.env,
                              capture_output=True, text=True)

    def test_install_creates_app_entry_and_uninstall_removes_it(self):
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)
        data = self.home / ".local" / "share"
        desktop = (data / "applications" / "ytdlp-gui.desktop").read_text()
        self.assertIn("Icon=ytdlp-gui", desktop)
        self.assertIn("Terminal=false", desktop)
        launcher = self.home / ".local" / "bin" / "ytdlp-gui"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertTrue((data / "icons" / "hicolor" / "256x256" / "apps" / "ytdlp-gui.png").exists())
        self.assertTrue((data / "ytdlp-gui" / "venv" / "bin" / "python").exists())
        self.assertTrue((data / "ytdlp-gui" / "app" / "ytdlp_gui" / "__main__.py").exists())
        self.assertEqual(self.run_script().returncode, 0)    # re-running upgrades in place
        self.assertEqual(self.run_script("--uninstall").returncode, 0)
        self.assertFalse(launcher.exists())
        self.assertFalse((data / "applications" / "ytdlp-gui.desktop").exists())
        self.assertFalse((data / "ytdlp-gui").exists())

    def test_root_wrapper_forwards_arguments(self):
        res = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], env=self.env,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("uninstall", res.stdout)

    def test_unknown_option_fails(self):
        self.assertNotEqual(self.run_script("--bogus").returncode, 0)


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "Linux installer")
class LinuxAutoInstallPythonTests(unittest.TestCase):
    """Runs the installer with a PATH that has no (usable) Python and fake apt-get/sudo."""

    TOOLS = ["bash", "sh", "env", "id", "dirname", "basename", "cat", "mkdir", "cp", "rm", "find",
             "install", "grep", "head", "sed", "chmod", "ln", "uname", "tr", "sort", "readlink"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.log = self.tmp / "calls.log"
        for tool in self.TOOLS:
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)
        real_python = os.path.realpath(sys.executable)
        (self.bin / "apt-get").write_text(
            '#!/bin/sh\necho "apt-get $*" >> "%s"\n'
            'if [ "$1" = install ]; then ln -sf "%s" "%s/python3"; fi\nexit 0\n'
            % (self.log, real_python, self.bin))
        (self.bin / "sudo").write_text('#!/bin/sh\necho "sudo $*" >> "%s"\nexec "$@"\n' % self.log)
        for name in ("apt-get", "sudo"):
            (self.bin / name).chmod(0o755)
        self.env = {"PATH": str(self.bin), "HOME": str(self.home), "YTDLP_GUI_SKIP_PIP": "1"}
        self.script = str(ROOT / "packaging" / "install_linux.sh")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, **extra_env):
        return subprocess.run([shutil.which("bash"), self.script], env={**self.env, **extra_env},
                              capture_output=True, text=True)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_missing_python_is_installed_then_used(self):
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        log = self.calls()
        self.assertIn("apt-get install -y python3 python3-venv python3-pip", log)
        self.assertTrue((self.home / ".local" / "share" / "ytdlp-gui" / "venv" / "bin" / "python").exists())

    def test_too_old_python_counts_as_missing(self):
        old = self.bin / "python3"
        old.write_text("#!/bin/sh\nexit 1\n")      # fails the 3.10+ check
        old.chmod(0o755)
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("apt-get install", self.calls())

    def test_auto_install_can_be_disabled(self):
        res = self.run_script(YTDLP_GUI_NO_AUTO_INSTALL="1")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Python 3.10+", res.stderr)
        self.assertEqual(self.calls(), "")

    def test_existing_python_is_not_reinstalled(self):
        (self.bin / "python3").symlink_to(os.path.realpath(sys.executable))
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.calls(), "")


class WindowsBatchTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / "packaging" / "build_windows.bat").read_bytes()
        self.text = self.raw.decode()

    def test_uses_windows_line_endings(self):
        self.assertEqual(self.raw.replace(b"\r\n", b"").count(b"\n"), 0)

    def test_detects_and_installs_python(self):
        for needle in (":find_python", ":install_python", "winget install -e --id Python.Python.3.12",
                       "python-3.12.10-amd64.exe", "start /wait", "YTDLP_GUI_NO_AUTO_INSTALL"):
            self.assertIn(needle, self.text)

    def test_every_called_label_exists(self):
        import re
        defined = set(re.findall(r"^:(\w+)", self.text, re.M))
        called = set(re.findall(r"(?:call|goto) :(\w+)", self.text))
        self.assertTrue(called <= defined, called - defined)


if __name__ == "__main__":
    unittest.main()
