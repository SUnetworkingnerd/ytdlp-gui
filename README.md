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

The Download tab has a "Cookies / login" box for age-restricted or members-only videos, or when YouTube says "sign in to confirm you're not a bot". There are three ways to log in, easiest first:

- **Sign in to YouTube in this app (recommended)**: click "Sign in...", pick a browser, and sign in to Google in the browser window that opens. Close that window when YouTube's home page shows, and the app finishes by itself. It keeps only the login cookies, in its own data folder (`~/.local/share/ytdlp-gui/` or `%LOCALAPPDATA%\\ytdlp-gui\\`), never your password. "Sign out" deletes them. Nothing to export and no cookies.txt to manage.
- **Use my browser's login** lets you pick Firefox, Chrome, Chromium, Edge, Brave, Vivaldi, Opera or Whale, plus an optional profile name or path (`--cookies-from-browser`).
- **Use a cookies.txt file** lets you browse to an exported Netscape-format file (`--cookies`).

The choice is remembered, applied to both "Fetch info" and downloads, and shown in the command preview. Cookies give access to your account, so never share the file.

How the in-app sign-in works, and why it avoids the usual cookie problems:

- Google refuses sign-in from embedded browsers ("This browser or app may not be secure"), so the app opens a **real, installed browser** instead. It starts on a **fresh, separate profile** (a temporary folder; on Linux `~/ytdlp-gui-login`, which is visible on purpose because Snap and Flatpak browsers can't use hidden folders), so your normal browser and its logins are never touched.
- When you close that browser, yt-dlp reads the new profile's cookies once (`--cookies-from-browser` with `--cookies`), the app keeps only the youtube.com and google.com cookies, and the temporary profile is deleted. Nothing reopens that session, so YouTube has no reason to rotate the cookies, which is the usual cause of "cookies are no longer valid" errors (yt-dlp recommends exactly this export-then-close approach).
- **Firefox is the most reliable choice and is listed first.** Its cookie file isn't encrypted, so yt-dlp can read it on every system. Chrome, Chromium, Brave, Edge and Vivaldi are also offered, but they encrypt cookies with a key that isn't always available (Chrome on Windows is a known problem). If the app says no login was found, try Firefox.
- Close the browser window before the app can read the login: the browser only saves its cookies to disk when it closes. If your browser hands its window to another process and the app can't tell it has closed, use the "I've signed in and closed the browser" button.
- yt-dlp rewrites its cookies file when it exits, so every yt-dlp process gets its own copy of the file (under `jobs/`, cleaned up after a week). Several downloads running at once can't corrupt the signed-in original.
- If "Fetch info" or a download starts failing with login errors weeks later, click "Sign in again...".
- Downloading a lot while signed in can get an account banned (temporarily or permanently), according to yt-dlp's own documentation. Use a secondary account, and a gentle pace for archives.

If a Chromium-based browser fails to hand over its cookies for the browser option, close the browser and try again, or use Firefox, a cookies.txt file or the in-app sign-in.

## Dark mode

Use the "Dark mode" button in the top right corner for a quick switch, or pick System, Light or Dark under Settings > Theme. "System" follows your OS colour scheme and changes with it. The choice is remembered. The app uses Qt's Fusion style in all three modes, so it looks the same on Linux and Windows.

## Archive full channels

The "Archive full channels..." button on the Download tab opens a dialog (it picks up the URLs you pasted):

- **Several channels at once**: put one channel URL per line (or use "Load from file..." for a text file; blank lines and lines starting with `#` are ignored, duplicates are dropped). Each channel becomes its own job in the Queue tab with its own progress, log and download archive, all using the settings in the dialog. How many run at the same time follows the "parallel downloads" number on the Queue tab; the rest wait their turn. With big channels, a low number plus "be gentle" is kinder to rate limits.

- Everything goes into your chosen folder, one sub-folder per channel, files named `date - title [id]`.
- A **download archive** file (`<channel>.archive.txt`) records finished videos, so you can stop and start again at any time and nothing is downloaded twice.
- Quality limit (best, 4K down to 480p, or audio only), plus a **video format** (MKV, MP4, WebM or keep original) and, for audio only, an **audio format** (best, MP3, M4A, Opus, FLAC). A **custom `-f` string** can replace the quality choice for full control; the command preview shows the result.
- Optional: metadata (embedded tags and chapters, info.json, thumbnail) and subtitles in all languages (no live chat).
- **Pace** is the user's choice, from fastest to kindest to rate limits. Gentle is the default.
  - **Max speed**: no pauses, 8 connections per download, and the Queue tab's "parallel downloads" is raised to at least 4 (up to 8, depending on your CPU), the same as the Queue tab's "Max speed" button. The fastest, and the most likely to hit rate limits (HTTP 429) or blocks.
  - **Normal**: yt-dlp's own pacing, no added pauses.
  - **Gentle**: a random 3 to 8 second pause between videos (1 second between requests, 2 between subtitle downloads).
  - **Extra gentle**: a random 10 to 30 second pause between videos (2 seconds between requests, 5 between subtitle downloads), and the queue runs only **one extra-gentle channel at a time**, because the pauses only slow their own process and several channels at once would add up to a fast request rate anyway. Roughly 100 to 150 videos an hour, so a 1,000-video channel takes about 8 hours (a rough estimate). Waiting channels show as Queued and start in turn; ordinary downloads still run alongside, within the "parallel downloads" limit.
  - If you tick "Use cookies / login" at Max speed or Normal, the dialog adds a note that yt-dlp warns heavy use can get an account banned. It is only a note; the choice stays yours.
  - Settings saved by earlier versions keep their meaning: "Be gentle" on becomes Gentle, off becomes Normal, and "Extra gentle" stays Extra gentle.
- **Shorts and live streams**: a plain YouTube channel URL already includes them; untick the option to get regular videos only.
- **Quick update** only fetches videos newer than the archive (`--break-on-existing`). It processes the Videos, Shorts and Streams tabs one at a time so none gets skipped.
- **Cookies are off for archives unless you tick "Use cookies / login"** in the dialog. The box is unticked every time and uses the choice made on the Download tab. A long run while logged in can get the account banned (temporarily or permanently), so only use it when the channel needs a login, ideally with a secondary account and a gentle pace.
- Your Options-tab settings (proxy, rate limit, ...) apply too. Each queue row shows "Running (5 of 312)" while it works, and Stop ends it cleanly.

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

These cover the Qt-free core (option generation, argument building, progress parsing, process control, cookie export and sign-in detection). The sign-in window and the real browser it launches need a display and have to be tried by hand. They use a fake option parser and fake processes, so they run without PySide6 or yt-dlp.

## Layout

```
ytdlp_gui/core/options_model.py   option tree + argument building (no Qt)
ytdlp_gui/core/runner.py          subprocess, progress parsing, stop handling (no Qt)
ytdlp_gui/core/metadata.py        yt-dlp --dump-single-json wrapper (no Qt)
ytdlp_gui/core/cookies.py         cookie choice -> yt-dlp arguments (no Qt)
ytdlp_gui/core/signin.py          in-app sign-in: browser discovery and launch, cookie export, login detection, per-job copies (no Qt)
ytdlp_gui/ui/signin_dialog.py     the "Sign in to YouTube" window (opens a real browser, then reads its cookies)
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
