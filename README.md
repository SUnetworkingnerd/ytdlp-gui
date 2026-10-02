# yt-dlp GUI

A cross-platform (Linux and Windows) desktop GUI for [yt-dlp](https://github.com/yt-dlp/yt-dlp), built with Python and PySide6.

## Install

yt-dlp is installed from its **GitHub master branch** (not PyPI): `requirements.txt` points pip at `https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz`. Each install or rebuild re-fetches it, and the Settings tab has an "Update yt-dlp from GitHub" button.

### Linux (installs as an app)

```
./install.sh
```

This creates a private venv in `~/.local/share/ytdlp-gui`, installs PySide6 and yt-dlp from GitHub, adds a `ytdlp-gui` command to `~/.local/bin`, installs the logo icons, and adds "yt-dlp GUI" to your application menu. No root needed. Run it again any time to upgrade; `./install.sh --uninstall` removes it.

If Python 3.10+ (with venv) is missing, the script installs it for you with apt, dnf, yum, pacman or zypper (it uses `sudo` and may ask for your password). Set `YTDLP_GUI_NO_AUTO_INSTALL=1` to turn that off. Git is not required. For ffmpeg and YouTube support install `ffmpeg` and a JavaScript runtime (deno recommended).

### Windows (builds an exe)

```
build_windows.bat
```

If Python 3.10+ is missing, the script installs Python 3.12 for your user account (no admin needed), using winget if you have it and otherwise the installer from python.org. Set `YTDLP_GUI_NO_AUTO_INSTALL=1` to turn that off. The script builds in its own venv, pulls yt-dlp from GitHub, and produces `dist\ytdlp-gui\ytdlp-gui.exe` with the logo as its icon. yt-dlp is bundled inside the exe, so nothing else needs installing. ffmpeg, ffprobe and deno are downloaded into a `tools` folder next to the exe (skip with `--no-tools`).

Options: `--onefile` (single exe, but slower to start), `--zip` (also make `dist\ytdlp-gui-windows.zip`), `--no-refresh` (skip re-fetching yt-dlp).

Note: the bundled ffmpeg is a GPL build; redistributing the result means following its license. Because yt-dlp is bundled, updating it means rebuilding, or pointing Settings > "yt-dlp executable" at a newer `yt-dlp.exe`.

### Run from source

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m ytdlp_gui
```

## How it covers "all options"

The Options tab is generated at startup from yt-dlp's own parser (`yt_dlp.options.create_parser()`), so every option of the installed yt-dlp version appears, grouped as in the yt-dlp README, searchable, with its help text. `--foo` / `--no-foo` pairs become one three-state dropdown. Options that print info and exit (`--list-formats`, `-J`, `--version`, ...) are hidden because they don't fit a download queue. `--format`, `--paths`, `--preset-alias`, `--batch-file`, the cookie options and the live options are controlled from the Download tab instead, so the two places can't conflict. Edit `HIDDEN` sets in `core/options_model.py` to change this.

## Cookies / login

The Download tab has a "Cookies / login" box, so you don't have to dig through the Options tab:

- **Use my browser's login** lets you pick Firefox, Chrome, Chromium, Edge, Brave, Vivaldi, Opera or Whale, plus an optional profile name or path (`--cookies-from-browser`).
- **Use a cookies.txt file** lets you browse to an exported Netscape-format file (`--cookies`).

The choice is remembered, applied to both "Fetch info" and downloads, and shown in the command preview. If a Chromium-based browser fails to hand over its cookies, close the browser and try again, or use Firefox or a cookies.txt file. Cookies give access to your account, so never share the file.

## Dark mode

Use the "Dark mode" button in the top right corner for a quick switch, or pick System, Light or Dark under Settings > Theme. "System" follows your OS colour scheme and changes with it. The choice is remembered. The app uses Qt's Fusion style in all three modes, so it looks the same on Linux and Windows.

## Archive a full channel

The "Archive full channel..." button on the Download tab opens a dialog (it picks up the first URL you pasted):

- Everything goes into your chosen folder, one sub-folder per channel, files named `date - title [id]`.
- A **download archive** file (`<channel>.archive.txt`) records finished videos, so you can stop and start again at any time and nothing is downloaded twice.
- Quality limit (best, 4K down to 480p, or audio only), merged to mkv.
- Optional: metadata (embedded tags and chapters, info.json, thumbnail), subtitles in all languages (no live chat), and "be gentle" random pauses between videos, which helps big channels avoid rate limits.
- **Shorts and live streams**: a plain YouTube channel URL already includes them; untick the option to get regular videos only.
- **Quick update** only fetches videos newer than the archive (`--break-on-existing`). It processes the Videos, Shorts and Streams tabs one at a time so none gets skipped.
- Your Options-tab settings (proxy, rate limit, ...) and the cookie choice apply too. The queue row shows "Running (5 of 312)" while it works, and Stop ends it cleanly.

The command preview in the dialog shows exactly what will run.

## Live streams

The Download tab has a "Live and scheduled streams" box:

- **Record from the beginning** adds `--live-from-start` (experimental in yt-dlp; supported on YouTube, Twitch, TVer and mellow-fan at the time of writing). Left unticked, yt-dlp records from the current moment.
- **Wait for a scheduled stream** adds `--wait-for-video MIN[-MAX]`.
- "Fetch info" reads `live_status` and shows a hint when the URL is live or upcoming.
- **Stop** in the Queue tab sends SIGINT (Linux) or CTRL_BREAK (Windows) so yt-dlp can finalize the recording. If it hasn't exited after 20 seconds it is terminated. On Windows this relies on the bundled or pip-installed yt-dlp; an external `yt-dlp.exe` may stop less gracefully.

## Tests

```
python -m unittest discover -s tests -v
```

These cover the Qt-free core (option generation, argument building, progress parsing, process control). They use a fake option parser and fake processes, so they run without PySide6 or yt-dlp.

## Layout

```
ytdlp_gui/core/options_model.py   option tree + argument building (no Qt)
ytdlp_gui/core/runner.py          subprocess, progress parsing, stop handling (no Qt)
ytdlp_gui/core/metadata.py        yt-dlp --dump-single-json wrapper (no Qt)
ytdlp_gui/ui/options_panel.py     generated options widgets
ytdlp_gui/ui/main_window.py       Download / Queue / Options / Settings tabs
ytdlp_gui/assets/                 logo, generated icons (packaging/make_icons.py)
install.sh                        wrapper for packaging/install_linux.sh
build_windows.bat                 wrapper for packaging\build_windows.bat
packaging/install_linux.sh        venv + launcher + icons + menu entry
packaging/build_windows.bat/.py   PyInstaller build of the exe
run_ytdlp_gui.py                  PyInstaller entry point (also runs embedded yt-dlp)
```

## Not yet done

Per-entry playlist checklist (use `--playlist-items` in Options for now), tray icon and notifications, and a scheduler.
