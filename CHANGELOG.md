# Changelog

## 1.3.0

- Fixed: long downloads of streams that have just ended ("post-live") failed part-way with
  "HTTP Error 403: Forbidden" on every fragment, and yt-dlp then skipped all remaining
  fragments, leaving a broken file. yt-dlp fetches the download links for such a stream once,
  and YouTube's links expire before a long stream is done. The app now spots the first 403,
  stops yt-dlp during its retry pause (before anything is skipped), fetches fresh links and
  resumes at the same fragment. The Queue shows "Links expired, refreshing (1 of 8)". If fresh
  links get no further twice in a row, the job fails with a clear reason instead of producing
  a file with gaps; Retry resumes later.
- Fragment retries now wait 1, 2, 4 ... 20 seconds instead of retrying instantly, which is
  gentler on YouTube and gives the check above time to act. A --retry-sleep set in the Options
  tab still wins.
- The "Fetch info" hint for post-live streams explains that waiting for YouTube to finish
  processing gives the best result.

## 1.2.0

- New Quality choice on the Download tab: Best available, 4K (2160p), 1440p, 1080p, 720p, 480p.
- Fixed: the "MP4 video" preset could never download 4K. yt-dlp's MP4 preset prefers H.264, which
  YouTube only offers up to 1080p. With Best or 4K selected, the MP4 now gets the full resolution
  (VP9 or AV1 video in MP4). Choose 1080p for the old, most compatible H.264 file.
- "Fetch info" shows the highest resolution the video has, and the hint under Quality says when
  the video doesn't reach the chosen quality, or when ffmpeg or deno is missing for 4K.

## 1.1.0

Fixes
- Quitting while downloading now waits for yt-dlp to finish writing (live recordings, merges) instead of closing right away, which could kill yt-dlp mid-write.
- Playlists and channel archives where only some items failed show "Done, N failed" instead of "Failed".
- Removing a running job no longer lets an extra download start before it has actually stopped.
- Loading or saving a broken options preset shows an error instead of failing silently.
- "Update yt-dlp" no longer force-reinstalls every dependency, checks pip's result, and shows the old and new version. Same for the Linux installer and the Windows build.
- The formats table can no longer build an invalid `-f` string such as `137+140+251`.
- Job logs are now actually written to disk (the code comment said they were).
- Each run of a job gets a fresh private copy of the signed-in cookies, including retries.

New
- Failure reasons in the Queue's Status column.
- Right-click menu in the Queue: open file or folder, copy URL or command, open log, retry, stop, remove. Double-click opens the file.
- The queue is saved and unfinished downloads resume after a restart (can be turned off).
- Settings are saved shortly after every change; preset and live options are remembered.
- yt-dlp version in Settings, with an optional check for a newer release on start.
- Drag and drop links onto the window.
- Smoother UI with many downloads: progress updates are limited to four per second per job.
- `pyproject.toml` (`pip install .` adds a `ytdlp-gui` command), GitHub Actions CI, ruff config, `.gitignore`.
