"""Build ytdlp-gui.exe with PyInstaller. Run it through packaging\\build_windows.bat.

Output (default, one folder):   dist\\ytdlp-gui\\ytdlp-gui.exe   (+ tools\\ffmpeg, deno)
Output (--onefile):             dist\\ytdlp-gui.exe              (+ dist\\tools\\)

yt-dlp is taken from the GitHub master branch and bundled into the exe. The exe
runs it by launching itself with `--ytdlp`, so no separate yt-dlp install is needed.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ytdlp_gui.core.runner import YTDLP_SPEC  # noqa: E402  (Qt-free module)

APP_NAME = "ytdlp-gui"
ICON = ROOT / "ytdlp_gui" / "assets" / "icon.ico"
ASSETS = ROOT / "ytdlp_gui" / "assets"
FFMPEG_URL = ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")
DENO_URL = ("https://github.com/denoland/deno/releases/latest/download/"
            "deno-x86_64-pc-windows-msvc.zip")

# Packages whose data files or native libraries PyInstaller would otherwise miss.
# yt_dlp_ejs ships the JavaScript that YouTube support needs.
COLLECT_ALL = ["yt_dlp", "yt_dlp_ejs", "certifi", "brotli", "brotlicffi", "websockets",
               "requests", "urllib3", "curl_cffi", "Cryptodome", "mutagen"]


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def refresh_ytdlp() -> None:
    step("Fetching the latest yt-dlp from GitHub (master)")
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                    "--force-reinstall", YTDLP_SPEC], check=True)
    out = subprocess.run([sys.executable, "-c", "import yt_dlp; print(yt_dlp.version.__version__)"],
                         capture_output=True, text=True, check=True)
    print("yt-dlp version:", out.stdout.strip())


def run_pyinstaller(onefile: bool) -> Path:
    step("Building the executable with PyInstaller")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", APP_NAME, "--icon", str(ICON),
           "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
           "--specpath", str(ROOT / "build"),
           "--add-data", f"{ASSETS}{os.pathsep}ytdlp_gui/assets",
           "--onefile" if onefile else "--onedir"]
    for pkg in COLLECT_ALL:
        if importlib.util.find_spec(pkg) is not None:
            cmd += ["--collect-all", pkg]
    cmd.append(str(ROOT / "run_ytdlp_gui.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)
    return ROOT / "dist" if onefile else ROOT / "dist" / APP_NAME


def download_tool(url: str, wanted: set[str], dest: Path) -> None:
    print(f"downloading {url}", flush=True)
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    dest.mkdir(parents=True, exist_ok=True)
    found = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            base = os.path.basename(name).lower()
            if base in wanted:
                (dest / base).write_bytes(archive.read(name))
                found.add(base)
    missing = wanted - found
    if missing:
        raise RuntimeError(f"{', '.join(sorted(missing))} not found in the downloaded archive")


def bundle_tools(out_dir: Path) -> None:
    step("Adding ffmpeg, ffprobe and deno to the tools folder")
    tools = out_dir / "tools"
    for label, url, wanted in (("ffmpeg", FFMPEG_URL, {"ffmpeg.exe", "ffprobe.exe"}),
                               ("deno", DENO_URL, {"deno.exe"})):
        try:
            download_tool(url, wanted, tools)
        except Exception as exc:  # noqa: BLE001 - a failed download must not fail the build
            print(f"warning: could not add {label}: {exc}\n"
                  f"         Put {', '.join(sorted(wanted))} into {tools} yourself.")


def make_zip(out_dir: Path, onefile: bool) -> Path:
    """Zip the build: the whole folder (onedir) or just exe + tools (onefile)."""
    target = ROOT / "dist" / f"{APP_NAME}-windows.zip"
    if target.exists():
        target.unlink()
    if onefile:
        members = [(out_dir / f"{APP_NAME}.exe", Path(f"{APP_NAME}.exe"))]
        tools = out_dir / "tools"
        if tools.is_dir():
            members += [(f, Path("tools") / f.name) for f in tools.iterdir()]
    else:
        members = [(f, Path(APP_NAME) / f.relative_to(out_dir))
                   for f in out_dir.rglob("*") if f.is_file()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for src, arc in members:
            archive.write(src, arc.as_posix())
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onefile", action="store_true",
                        help="single exe instead of a folder (slower to start)")
    parser.add_argument("--no-tools", action="store_true",
                        help="do not download ffmpeg/ffprobe/deno into the tools folder")
    parser.add_argument("--no-refresh", action="store_true",
                        help="skip re-fetching yt-dlp from GitHub before building")
    parser.add_argument("--zip", action="store_true", help="also create a .zip of the result")
    args = parser.parse_args()

    if os.name != "nt":
        print("warning: this script builds a Windows exe and is meant to run on Windows.")
    if not ICON.exists():
        print(f"error: {ICON} is missing (run packaging/make_icons.py)")
        return 1
    if not args.no_refresh:
        refresh_ytdlp()
    out_dir = run_pyinstaller(args.onefile)
    if not args.no_tools:
        bundle_tools(out_dir)
    if args.zip:
        step("Creating zip")
        print("created", make_zip(out_dir, args.onefile))
    exe = out_dir / f"{APP_NAME}.exe"
    step(f"Done: {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
