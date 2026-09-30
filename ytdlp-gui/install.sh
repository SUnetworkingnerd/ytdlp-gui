#!/usr/bin/env bash
# Linux: installs yt-dlp GUI as an app (venv + menu entry + icon).
# Usage: ./install.sh            install or upgrade
#        ./install.sh --uninstall
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/packaging/install_linux.sh" "$@"
