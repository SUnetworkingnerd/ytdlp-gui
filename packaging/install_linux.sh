#!/usr/bin/env bash
# Installs yt-dlp GUI for the current user: private venv, launcher command,
# icons and an application-menu entry. Root is only used if Python must be installed.
#
#   ./packaging/install_linux.sh              install or upgrade
#   ./packaging/install_linux.sh --uninstall  remove everything it installed
#
# If Python 3.10+ (with venv) is missing it is installed with your package manager
# (apt, dnf, yum, pacman or zypper; uses sudo). Set YTDLP_GUI_NO_AUTO_INSTALL=1 to
# turn that off.
set -euo pipefail

APP_ID="ytdlp-gui"
APP_NAME="yt-dlp GUI"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_DIR="$HOME/.local/bin"
INSTALL_DIR="$DATA_HOME/$APP_ID"
ICON_ROOT="$DATA_HOME/icons/hicolor"
APPS_DIR="$DATA_HOME/applications"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
LAUNCHER="$BIN_DIR/$APP_ID"
PYTHON="${PYTHON:-}"

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Finds a Python 3.10+ that can create venvs; sets $PYTHON.
find_python() {
    local cand
    for cand in "$PYTHON" python3.13 python3.12 python3.11 python3.10 python3 python; do
        [ -n "$cand" ] || continue
        command -v "$cand" >/dev/null 2>&1 || continue
        if "$cand" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON="$cand"
            return 0
        fi
    done
    return 1
}

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

# Installs Python (+ venv and pip) with the system package manager.
install_python() {
    if [ "${YTDLP_GUI_NO_AUTO_INSTALL:-0}" = "1" ]; then
        return 1
    fi
    if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
        warn "sudo is not available, so Python can't be installed automatically."
        return 1
    fi
    if command -v apt-get >/dev/null 2>&1; then
        say "Installing Python with apt (you may be asked for your password)"
        run_as_root apt-get update && run_as_root apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf >/dev/null 2>&1; then
        say "Installing Python with dnf (you may be asked for your password)"
        run_as_root dnf install -y python3 python3-pip
    elif command -v yum >/dev/null 2>&1; then
        say "Installing Python with yum (you may be asked for your password)"
        run_as_root yum install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        say "Installing Python with pacman (you may be asked for your password)"
        run_as_root pacman -S --needed --noconfirm python python-pip
    elif command -v zypper >/dev/null 2>&1; then
        say "Installing Python with zypper (you may be asked for your password)"
        run_as_root zypper --non-interactive install python3 python3-pip
    else
        warn "no supported package manager found (apt, dnf, yum, pacman, zypper)."
        return 1
    fi
}

refresh_caches() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f "$ICON_ROOT/index.theme" ]; then
        gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true
    fi
}

uninstall() {
    say "Removing $APP_NAME"
    rm -rf "$INSTALL_DIR"
    rm -f "$LAUNCHER" "$DESKTOP_FILE"
    rm -f "$ICON_ROOT"/*/apps/"$APP_ID".png
    refresh_caches
    echo "Removed. Your saved settings (if any) are in ~/.config/$APP_ID and were left alone."
}

case "${1:-}" in
    --uninstall) uninstall; exit 0 ;;
    -h|--help)   sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "")          ;;
    *)           die "unknown option: $1 (try --help)" ;;
esac

# ---- checks ---------------------------------------------------------------
if ! find_python; then
    say "Python 3.10 or newer (with venv) was not found"
    if install_python && find_python; then
        :
    else
        die "Python 3.10+ with venv support is required and could not be installed automatically.
       Install it with your package manager (Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip)
       and run this again. If your distro's Python is older than 3.10, install a newer one and run:
       PYTHON=/path/to/python3.12 $0"
    fi
fi
say "Using $("$PYTHON" -V 2>&1)"
[ -d "$SRC_DIR/ytdlp_gui" ] || die "run this script from inside the project (ytdlp_gui/ not found in $SRC_DIR)."

# ---- copy the app ---------------------------------------------------------
say "Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$APPS_DIR"
rm -rf "$INSTALL_DIR/app"
mkdir -p "$INSTALL_DIR/app"
cp -r "$SRC_DIR/ytdlp_gui" "$INSTALL_DIR/app/"
find "$INSTALL_DIR/app" -name '__pycache__' -type d -prune -exec rm -rf {} +
cp "$SRC_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

# ---- virtual environment --------------------------------------------------
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    say "Creating virtual environment"
    "$PYTHON" -m venv "$INSTALL_DIR/venv" || die "could not create a venv. On Debian/Ubuntu run: sudo apt install python3-venv"
fi
VENV_PY="$INSTALL_DIR/venv/bin/python"

if [ "${YTDLP_GUI_SKIP_PIP:-0}" = "1" ]; then
    warn "skipping pip install (YTDLP_GUI_SKIP_PIP=1)"
else
    say "Installing PySide6 and yt-dlp from GitHub (PySide6 is large, this can take a few minutes)"
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r "$INSTALL_DIR/requirements.txt"
    # Always re-fetch yt-dlp from the GitHub master branch so re-running this
    # script upgrades it even if pip thinks the version is unchanged.
    YTDLP_SPEC="$(grep -E '^yt-dlp' "$INSTALL_DIR/requirements.txt" | head -n1)"
    [ -n "$YTDLP_SPEC" ] || die "no yt-dlp line found in requirements.txt"
    "$VENV_PY" -m pip install --upgrade --force-reinstall "$YTDLP_SPEC"
    say "yt-dlp version: $("$VENV_PY" -c 'import yt_dlp; print(yt_dlp.version.__version__)')"
fi

# ---- launcher command -----------------------------------------------------
say "Writing launcher $LAUNCHER"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$INSTALL_DIR/app\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV_PY" -m ytdlp_gui "\$@"
EOF
chmod +x "$LAUNCHER"

# ---- icons ----------------------------------------------------------------
say "Installing icons"
for png in "$SRC_DIR"/ytdlp_gui/assets/icons/*.png; do
    size="$(basename "$png" .png)"
    install -Dm644 "$png" "$ICON_ROOT/$size/apps/$APP_ID.png"
done

# ---- application menu entry -----------------------------------------------
say "Creating menu entry"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
GenericName=Video Downloader
Comment=Graphical front end for yt-dlp
Exec="$LAUNCHER"
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Network;Utility;
Keywords=youtube;video;download;yt-dlp;live;stream;
StartupWMClass=$APP_ID
StartupNotify=true
EOF
chmod 644 "$DESKTOP_FILE"
refresh_caches

# ---- friendly warnings ----------------------------------------------------
command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1 \
    || warn "ffmpeg/ffprobe not found. Needed to merge and convert. Install ffmpeg with your package manager."
for js in deno node bun qjs; do command -v "$js" >/dev/null 2>&1 && js_found=1 && break; done
[ "${js_found:-0}" = "1" ] \
    || warn "no JavaScript runtime found. Full YouTube support needs one (deno recommended). node/bun/quickjs also work but must be enabled with --js-runtimes in the Options tab."
if command -v ldconfig >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q 'libxcb-cursor'; then
    warn "libxcb-cursor0 not found. On X11 (or XWayland) Qt may fail to start without it (Debian/Ubuntu: sudo apt install libxcb-cursor0)."
fi
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH, so the 'ytdlp-gui' command won't be found in a terminal. The menu entry still works." ;;
esac

say "Done. Find \"$APP_NAME\" in your application menu, or run: $APP_ID"
echo "Uninstall any time with: $0 --uninstall"
