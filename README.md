# Custom Video Podcasts

A small, static podcast site published with GitHub Pages.

- Site: <https://iamadamdev.github.io/podcasts/>
- RSS feed: <https://iamadamdev.github.io/podcasts/feed.xml>

The RSS feed and web players use MP3 files in `audio_files/`, hosted directly
by GitHub Pages. The original M4A briefing is also available as a GitHub Release
asset because the source file is larger than Git's 100 MB per-file limit.

## Import Meet Kevin videos

The updater finds completed videos published to Meet Kevin's channel in an
exact rolling time window, downloads audio only with `yt-dlp`, and rebuilds the
RSS feed and landing page:

```bash
python3 scripts/update_meet_kevin.py --hours 24
```

Preview qualifying videos without changing files:

```bash
python3 scripts/update_meet_kevin.py --hours 24 --dry-run
```

The script is noninteractive and idempotent, so the same command can later run
from a 12-hour scheduler. It requires `yt-dlp`, `ffmpeg`, and `ffprobe`; the
Homebrew `ffmpeg` package supplies both FFmpeg tools.
