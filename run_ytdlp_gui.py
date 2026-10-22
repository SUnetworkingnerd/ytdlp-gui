"""PyInstaller entry point.

`ytdlp-gui.exe --ytdlp ARGS...` runs the bundled yt-dlp (used for downloads and
metadata fetches); anything else starts the GUI.
"""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "--ytdlp":
    from ytdlp_gui.core.runner import run_embedded
    sys.exit(run_embedded(sys.argv[2:]))

from ytdlp_gui.__main__ import main  # noqa: E402

sys.exit(main())
