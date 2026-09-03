#!/usr/bin/env python3
"""Import recent Meet Kevin YouTube videos into the podcast feed.

The script is deliberately noninteractive and idempotent so it can be run by a
future scheduler. It discovers videos with yt-dlp, applies an exact rolling
time cutoff, downloads 96 kbps MP3 audio, updates episodes.json, and rebuilds
feed.xml plus the episode cards in index.html.
"""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from xml.sax.saxutils import escape as xml_escape


ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "audio_files"
EPISODES_PATH = ROOT / "episodes.json"
FEED_PATH = ROOT / "feed.xml"
INDEX_PATH = ROOT / "index.html"

CHANNEL_URL = "https://www.youtube.com/@MeetKevin/videos"
EXPECTED_CHANNEL_ID = "UCUvvj5lwue7PspotMDjk5UA"
SITE_URL = "https://iamadamdev.github.io/podcasts/"
FEED_URL = f"{SITE_URL}feed.xml"
SHOW_TITLE = "Custom Video Podcasts"
SHOW_DESCRIPTION = (
    "Audio editions of selected YouTube videos and original audio briefings."
)

EPISODES_START = "      <!-- EPISODES_START -->"
EPISODES_END = "      <!-- EPISODES_END -->"


def log(message: str) -> None:
    print(message, flush=True)


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def require_tools() -> None:
    missing = [name for name in ("yt-dlp", "ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"Missing required tools: {', '.join(missing)}")


def load_episodes() -> list[dict[str, Any]]:
    with EPISODES_PATH.open(encoding="utf-8") as handle:
        episodes = json.load(handle)
    if not isinstance(episodes, list):
        raise RuntimeError("episodes.json must contain a JSON array")
    return episodes


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    os.chmod(temporary_path, 0o644)
    os.replace(temporary_path, path)


def save_episodes(episodes: list[dict[str, Any]]) -> None:
    content = json.dumps(episodes, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    atomic_write(EPISODES_PATH, content)


def flat_video_ids(max_scan: int) -> list[str]:
    output = run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--playlist-end",
            str(max_scan),
            "--print",
            "%(id)s",
            "--no-warnings",
            CHANNEL_URL,
        ],
        capture=True,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def video_metadata(video_id: str) -> dict[str, Any] | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        output = run(
            [
                "yt-dlp",
                "--dump-single-json",
                "--skip-download",
                "--no-playlist",
                "--no-warnings",
                url,
            ],
            capture=True,
        )
    except subprocess.CalledProcessError:
        log(f"Warning: could not read metadata for {url}; skipping")
        return None
    return json.loads(output)


def discover_recent_videos(hours: float, max_scan: int) -> list[dict[str, Any]]:
    now = int(time.time())
    cutoff = now - int(hours * 3600)
    candidates: list[dict[str, Any]] = []
    consecutive_old = 0

    for video_id in flat_video_ids(max_scan):
        metadata = video_metadata(video_id)
        if not metadata:
            continue
        if metadata.get("channel_id") != EXPECTED_CHANNEL_ID:
            raise RuntimeError(
                f"Unexpected channel for {video_id}: {metadata.get('channel_id')}"
            )

        timestamp = metadata.get("timestamp") or metadata.get("release_timestamp")
        if not timestamp:
            log(f"Warning: {video_id} has no publication timestamp; skipping")
            continue

        if timestamp < cutoff:
            consecutive_old += 1
            if consecutive_old >= 3:
                break
            continue

        consecutive_old = 0
        if timestamp > now + 600:
            log(f"Skipping future premiere {video_id}")
            continue
        if metadata.get("live_status") in {"is_live", "is_upcoming"}:
            log(f"Skipping active or upcoming livestream {video_id}")
            continue
        metadata["timestamp"] = int(timestamp)
        candidates.append(metadata)

    return sorted(candidates, key=lambda item: int(item["timestamp"]))


def download_audio(metadata: dict[str, Any]) -> Path:
    video_id = metadata["id"]
    published = time.gmtime(int(metadata["timestamp"]))
    date_part = time.strftime("%Y%m%d", published)
    relative_path = Path("audio_files") / f"meet-kevin-{date_part}-{video_id}.mp3"
    destination = ROOT / relative_path
    if destination.exists():
        return relative_path

    output_template = str(destination.with_suffix(".%(ext)s"))
    log(f"Downloading audio: {metadata['title']}")
    run(
        [
            "yt-dlp",
            "--no-playlist",
            "--format",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "96K",
            "--embed-metadata",
            "--no-overwrites",
            "--output",
            output_template,
            metadata["webpage_url"],
        ]
    )
    if not destination.exists():
        raise RuntimeError(f"yt-dlp did not produce {destination}")
    if destination.stat().st_size >= 100_000_000:
        raise RuntimeError(
            f"{destination.name} exceeds GitHub's 100 MB file limit; "
            "reduce the audio bitrate before publishing"
        )
    return relative_path


def probe_duration(path: Path) -> int:
    output = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return max(1, round(float(output.strip())))


def duration_text(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def compact_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} hr {minutes} min"
    return f"{minutes} min"


def published_rfc2822(timestamp: int) -> str:
    return email.utils.formatdate(timestamp, localtime=False, usegmt=True)


def render_feed(episodes: list[dict[str, Any]]) -> str:
    latest = max(int(episode["published_timestamp"]) for episode in episodes)
    items: list[str] = []
    for episode in sorted(
        episodes, key=lambda item: int(item["published_timestamp"]), reverse=True
    ):
        title = xml_escape(str(episode["title"]))
        author = xml_escape(str(episode["author"]))
        description = xml_escape(str(episode["description"]))
        filename = str(episode["filename"])
        enclosure_url = xml_escape(f"{SITE_URL}{filename}", {'"': "&quot;"})
        enclosure_length = (ROOT / filename).stat().st_size
        source_url = episode.get("source_url")
        link_line = (
            f"      <link>{xml_escape(str(source_url))}</link>\n" if source_url else ""
        )
        item_lines = [
            "    <item>",
            f"      <title>{title}</title>",
            f"      <itunes:title>{title}</itunes:title>",
            f"      <description>{description}</description>",
        ]
        if link_line:
            item_lines.append(link_line.rstrip("\n"))
        item_lines.extend(
            [
                f"      <pubDate>{published_rfc2822(int(episode['published_timestamp']))}</pubDate>",
                f"      <guid isPermaLink=\"false\">{xml_escape(str(episode['guid']))}</guid>",
                "      <enclosure",
                f"        url=\"{enclosure_url}\"",
                f"        length=\"{enclosure_length}\"",
                "        type=\"audio/mpeg\" />",
            ]
        )
        item_lines.extend(
            [
                f"      <itunes:author>{author}</itunes:author>",
                f"      <itunes:duration>{duration_text(int(episode['duration_seconds']))}</itunes:duration>",
                f"      <itunes:episode>{int(episode['episode_number'])}</itunes:episode>",
                "      <itunes:episodeType>full</itunes:episodeType>",
                "      <itunes:explicit>false</itunes:explicit>",
                "    </item>",
            ]
        )
        items.append(
            "\n".join(item_lines)
        )

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:atom="http://www.w3.org/2005/Atom"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{xml_escape(SHOW_TITLE)}</title>
    <link>{SITE_URL}</link>
    <atom:link href="{FEED_URL}" rel="self" type="application/rss+xml" />
    <description>{xml_escape(SHOW_DESCRIPTION)}</description>
    <language>en-us</language>
    <copyright>&#xA9; 2026 Adam</copyright>
    <lastBuildDate>{published_rfc2822(latest)}</lastBuildDate>
    <generator>scripts/update_mk.py</generator>
    <ttl>60</ttl>

    <image>
      <url>{SITE_URL}cover.png</url>
      <title>{xml_escape(SHOW_TITLE)}</title>
      <link>{SITE_URL}</link>
    </image>

    <itunes:author>Adam</itunes:author>
    <itunes:summary>{xml_escape(SHOW_DESCRIPTION)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Technology" />
    <itunes:image href="{SITE_URL}cover.png" />

{chr(10).join(items)}
  </channel>
</rss>
'''


def validate_episodes(episodes: list[dict[str, Any]]) -> None:
    if not episodes:
        raise RuntimeError("episodes.json must contain at least one episode")

    for field in ("guid", "episode_number", "filename"):
        values = [episode[field] for episode in episodes]
        if len(values) != len(set(values)):
            raise RuntimeError(f"Duplicate episode {field} in episodes.json")

    source_ids = [episode["source_id"] for episode in episodes if episode.get("source_id")]
    if len(source_ids) != len(set(source_ids)):
        raise RuntimeError("Duplicate source_id in episodes.json")

    for episode in episodes:
        path = ROOT / str(episode["filename"])
        if not path.is_file():
            raise RuntimeError(f"Missing episode audio: {path}")
        if path.stat().st_size >= 100_000_000:
            raise RuntimeError(f"Episode audio exceeds GitHub's 100 MB limit: {path}")


def render_episode_cards(episodes: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for episode in sorted(
        episodes, key=lambda item: int(item["published_timestamp"]), reverse=True
    ):
        title = html.escape(str(episode["title"]))
        author = html.escape(str(episode["author"]))
        description = html.escape(str(episode["description"]))
        filename = html.escape(str(episode["filename"]), quote=True)
        published = time.strftime(
            "%b %-d, %Y", time.localtime(int(episode["published_timestamp"]))
        )
        source_url = episode.get("source_url")
        extra_links: list[str] = []
        if source_url:
            extra_links.append(
                f'<a href="{html.escape(str(source_url), quote=True)}">Original video</a>'
            )
        extra_links_html = f" · {' · '.join(extra_links)}" if extra_links else ""
        cards.append(
            f'''        <article>
          <div class="episode-number">{author} · {published} · {compact_duration(int(episode["duration_seconds"]))}</div>
          <h3>{title}</h3>
          <p>{description}</p>
          <audio controls preload="metadata" src="{filename}"></audio>
          <div class="episode-links"><a class="download" href="{filename}">Download audio</a>{extra_links_html}</div>
        </article>'''
        )
    return "\n".join(cards)


def rebuild_outputs(episodes: list[dict[str, Any]]) -> None:
    atomic_write(FEED_PATH, render_feed(episodes))

    index = INDEX_PATH.read_text(encoding="utf-8")
    if EPISODES_START not in index or EPISODES_END not in index:
        raise RuntimeError("index.html is missing episode marker comments")
    prefix, remainder = index.split(EPISODES_START, 1)
    _, suffix = remainder.split(EPISODES_END, 1)
    replacement = (
        f"{EPISODES_START}\n{render_episode_cards(episodes)}\n{EPISODES_END}"
    )
    atomic_write(INDEX_PATH, prefix + replacement + suffix)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours",
        type=float,
        default=24,
        help="rolling upload window in hours (default: 24)",
    )
    parser.add_argument(
        "--max-scan",
        type=int,
        default=30,
        help="maximum number of channel entries to inspect (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list qualifying videos without downloading or changing files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hours <= 0 or args.max_scan <= 0:
        raise RuntimeError("--hours and --max-scan must be positive")
    require_tools()
    AUDIO_DIR.mkdir(exist_ok=True)

    episodes = load_episodes()
    known_source_ids = {
        episode["source_id"] for episode in episodes if episode.get("source_id")
    }
    candidates = discover_recent_videos(args.hours, args.max_scan)
    new_candidates = [item for item in candidates if item["id"] not in known_source_ids]

    log(
        f"Found {len(candidates)} qualifying video(s) in the last {args.hours:g} "
        f"hours; {len(new_candidates)} are new."
    )
    for metadata in new_candidates:
        log(f"  {metadata['id']}  {metadata['title']}")
    if args.dry_run:
        return 0

    next_episode_number = max(
        (int(episode["episode_number"]) for episode in episodes), default=0
    ) + 1
    for metadata in new_candidates:
        relative_path = download_audio(metadata)
        absolute_path = ROOT / relative_path
        episodes.append(
            {
                "author": "Meet Kevin",
                "description": (
                    "Audio edition of Meet Kevin's YouTube video. "
                    f"Original video: {metadata['webpage_url']}"
                ),
                "duration_seconds": probe_duration(absolute_path),
                "episode_number": next_episode_number,
                "filename": relative_path.as_posix(),
                "guid": f"meet-kevin-{metadata['id']}",
                "published_timestamp": int(metadata["timestamp"]),
                "source_id": metadata["id"],
                "source_url": metadata["webpage_url"],
                "title": metadata["title"],
            }
        )
        next_episode_number += 1

    validate_episodes(episodes)
    save_episodes(episodes)
    rebuild_outputs(episodes)
    log(f"Updated feed and site with {len(episodes)} total episode(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
