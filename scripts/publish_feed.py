#!/usr/bin/env python3
"""Update the last 96 hours of the feed, commit changes, and push to main."""

from __future__ import annotations

from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def log(message: str) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}", flush=True)


def run(*command: str, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def publish() -> int:
    lock_path = Path(run("git", "rev-parse", "--git-path", "publish-feed.lock", capture=True))
    if not lock_path.is_absolute():
        lock_path = ROOT / lock_path
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another feed update is already running; skipping.")
            return 0

        log("Starting daily feed update.")
        if run("git", "branch", "--show-current", capture=True) != "main":
            raise RuntimeError("Switch this checkout to main before publishing.")
        if run("git", "status", "--porcelain", "--untracked-files=all", capture=True):
            raise RuntimeError(
                "The checkout has uncommitted changes. Commit or move them before retrying."
            )

        # Stop if histories diverge; never overwrite remote or local commits.
        run("git", "pull", "--ff-only", "origin", "main")
        run(sys.executable, str(ROOT / "scripts" / "update_mk.py"), "--hours", "96")

        episodes = json.loads((ROOT / "episodes.json").read_text(encoding="utf-8"))
        audio_paths = sorted({episode["filename"] for episode in episodes})
        for filename in audio_paths:
            path = (ROOT / filename).resolve()
            if path.parent != ROOT / "audio_files" or path.suffix != ".mp3":
                raise RuntimeError(f"Unexpected episode audio path: {filename}")

        # Stage only the feed, site, manifest, and audio referenced by the manifest.
        publish_paths = ["episodes.json", "feed.xml", "index.html", *audio_paths]
        run("git", "add", "--", *publish_paths)
        if run("git", "diff", "--cached", "--name-only", "--", *publish_paths, capture=True):
            run(
                "git", "commit",
                "-m", "Refresh Meet Kevin podcast feed",
                "-m", (
                    "Import new videos from the last 96 hours and regenerate the RSS "
                    "feed and episode cards.\n\n"
                    "Validated episode IDs, audio files, and file sizes with scripts/update_mk.py."
                ),
                # Leave unrelated changes staged by a person during the download alone.
                "--only", "--", *publish_paths,
            )
        else:
            log("No feed changes to commit.")

        # Still push on a no-op run to retry a commit left by a failed earlier push.
        run("git", "push", "origin", "HEAD:main")
        log("Feed update and push completed successfully.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(publish())
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        log(f"ERROR: {error}")
        raise SystemExit(1)
