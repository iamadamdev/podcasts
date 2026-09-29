#!/usr/bin/env python3
"""Import recent episodes, prune episodes older than 30 days, and push to main."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
COMMIT_NAME = "Adam"
COMMIT_EMAIL = "36013816+iamadamdev@users.noreply.github.com"


def log(message: str) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}", flush=True)


def run(*command: str, capture: bool = False, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT if cwd is None else cwd,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            # Explicit author and committer values override Git config and the
            # calling shell's identity, including the launch agent environment.
            "GIT_AUTHOR_NAME": COMMIT_NAME,
            "GIT_AUTHOR_EMAIL": COMMIT_EMAIL,
            "GIT_COMMITTER_NAME": COMMIT_NAME,
            "GIT_COMMITTER_EMAIL": COMMIT_EMAIL,
        },
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def episode_audio_paths(root: Path) -> set[str]:
    episodes = json.loads((root / "episodes.json").read_text(encoding="utf-8"))
    audio_paths = {episode["filename"] for episode in episodes}
    for filename in audio_paths:
        relative = Path(filename)
        path = root / relative
        if (relative.parent != Path("audio_files") or relative.suffix != ".mp3"
                or path.is_symlink() or path.resolve().parent != root / "audio_files"):
            raise RuntimeError(f"Unexpected episode audio path: {filename}")
    return audio_paths


def recovery_directory() -> Path:
    return Path(run("git", "rev-parse", "--absolute-git-dir", capture=True)) / "publish-audio-recovery"


def validate_recovery_audio(root: Path, filename: str, audio: Path) -> None:
    """Only recover complete audio that agrees with the already committed feed."""
    entries = json.loads((root / "episodes.json").read_text(encoding="utf-8"))
    entry = next(item for item in entries if item["filename"] == filename)
    items = ET.parse(root / "feed.xml").findall("channel/item")
    enclosure = next((item.find("enclosure") for item in items
                      if item.findtext("guid") == entry["guid"]), None)
    if (audio.is_symlink() or not audio.is_file() or enclosure is None
            or not enclosure.get("url", "").endswith("/" + filename)
            or not 0 < audio.stat().st_size < 100_000_000
            or audio.stat().st_size != int(enclosure.get("length", "0"))):
        raise RuntimeError(f"Cannot recover audio that does not match the committed feed: {filename}")
    duration = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", str(audio), capture=True))
    if not math.isfinite(duration) or abs(duration - int(entry["duration_seconds"])) > 1:
        raise RuntimeError(f"Cannot recover audio with an unexpected duration: {filename}")
    run("ffmpeg", "-v", "error", "-xerror", "-i", str(audio), "-f", "null", "-")


def preserve_recoverable_audio() -> None:
    # Refuse tracked/staged work and unrelated untracked files. The sole exception
    # is audio already declared in the unchanged, committed episode manifest.
    changed = run("git", "status", "--porcelain", "--untracked-files=no", capture=True)
    untracked = set(filter(None, run("git", "ls-files", "--others", "--exclude-standard",
                                     "-z", capture=True).split("\0")))
    if changed or untracked - episode_audio_paths(ROOT):
        raise RuntimeError("The checkout has uncommitted changes. Commit or move them before retrying.")
    for filename in sorted(untracked):
        validate_recovery_audio(ROOT, filename, ROOT / filename)
    cache = recovery_directory()
    for filename in sorted(untracked):
        source = ROOT / filename
        backup = cache / Path(filename).name
        cache.mkdir(exist_ok=True)
        if not backup.exists():
            shutil.copy2(source, backup)
        if backup.is_symlink() or backup.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Recovery copy differs from local audio; preserving both: {filename}")
        # Preserve the bytes across failed runs and abrupt process termination.
        source.unlink()
        log(f"Preserved omitted episode audio for automatic recovery: {filename}")


def restore_recovery_audio(checkout: Path) -> list[str]:
    restored = []
    cache = recovery_directory()
    for filename in sorted(episode_audio_paths(checkout)):
        destination = checkout / filename
        backup = cache / Path(filename).name
        if not destination.exists() and backup.exists():
            validate_recovery_audio(checkout, filename, backup)
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(backup, destination)
            restored.append(filename)
    return restored


def fast_forward(checkout: Path, starting_head: str) -> str:
    prepared_head = run("git", "rev-parse", "HEAD", capture=True, cwd=checkout)
    if (run("git", "branch", "--show-current", capture=True) != "main"
            or run("git", "rev-parse", "HEAD", capture=True) != starting_head):
        raise RuntimeError("The checkout changed during the update; retry after finishing that work.")
    run("git", "merge", "--ff-only", prepared_head)
    return prepared_head


def validate_staged_audio(checkout: Path) -> None:
    tracked = set(run("git", "ls-files", "-z", capture=True, cwd=checkout).split("\0"))
    for filename in episode_audio_paths(checkout):
        path = checkout / filename
        if filename not in tracked or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Refusing to publish a feed without its audio: {filename}")


@contextmanager
def isolated_checkout():
    # Keep downloads and partially written outputs out of the scheduled checkout.
    # Even an interrupted process can only leave files under Git's private dir.
    git_dir = Path(run("git", "rev-parse", "--absolute-git-dir", capture=True))
    with tempfile.TemporaryDirectory(prefix="publish-feed-", dir=git_dir) as temporary:
        checkout = Path(temporary) / "checkout"
        run("git", "worktree", "add", "--detach", str(checkout), "HEAD", capture=True)
        try:
            yield checkout
        finally:
            run("git", "worktree", "remove", "--force", str(checkout), capture=True)


def publish(hours: float = 96, max_scan: int = 30) -> int:
    if hours <= 0 or max_scan <= 0:
        raise RuntimeError("--hours and --max-scan must be positive")
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
        preserve_recoverable_audio()

        # Stop if histories diverge; never overwrite remote or local commits.
        run("git", "pull", "--ff-only", "origin", "main")
        starting_head = run("git", "rev-parse", "HEAD", capture=True)
        with isolated_checkout() as checkout:
            restored = restore_recovery_audio(checkout)
            if restored:
                run("git", "add", "--", *restored, cwd=checkout)
                validate_staged_audio(checkout)
                run("git", "commit", "-m", "Recover omitted podcast audio files",
                    "-m", "Publish locally recovered MP3s referenced by the committed feed. "
                    "Validated enclosure sizes, durations, and complete audio decoding before recovery.",
                    "--only", "--", *restored, cwd=checkout)
                starting_head = fast_forward(checkout, starting_head)
            # Also retry a previously committed repair before downloading new episodes.
            cache = recovery_directory()
            if cache.exists():
                validate_staged_audio(checkout)
                run("git", "push", "origin", "HEAD:main")
                if restored:
                    log(f"Recovered and published {len(restored)} omitted audio file(s).")
                for filename in episode_audio_paths(checkout):
                    backup = cache / Path(filename).name
                    if backup.is_file() and not backup.is_symlink() and backup.read_bytes() == (checkout / filename).read_bytes():
                        backup.unlink()
                if not any(cache.iterdir()):
                    cache.rmdir()
            previous_audio_paths = episode_audio_paths(checkout)
            update_command = [sys.executable, str(checkout / "scripts" / "update_mk.py"),
                              "--hours", f"{hours:g}"]
            if max_scan != 30:
                update_command.extend(["--max-scan", str(max_scan)])
            run(*update_command, cwd=checkout)

            # Include the old manifest's paths so removed MP3s are committed as well.
            audio_paths = previous_audio_paths | episode_audio_paths(checkout)
            publish_paths = ["episodes.json", "feed.xml", "index.html", *sorted(audio_paths)]
            run("git", "add", "--", *publish_paths, cwd=checkout)
            validate_staged_audio(checkout)
            if run("git", "diff", "--cached", "--name-only", "--", *publish_paths,
                   capture=True, cwd=checkout):
                run(
                    "git", "commit",
                    "-m", "Refresh Meet Kevin podcast feed",
                    "-m", (
                        f"Import new videos from the last {hours:g} hours, remove episodes and "
                        "MP3s older than 30 days, and regenerate the RSS feed and episode cards.\n\n"
                        "Validated episode IDs, audio files, and file sizes with scripts/update_mk.py."
                    ),
                    "--only", "--", *publish_paths, cwd=checkout,
                )
            else:
                log("No feed changes to commit.")

            # A normal fast-forward preserves concurrent edits or refuses conflicts.
            fast_forward(checkout, starting_head)

        # Still push on a no-op run to retry a commit left by a failed earlier push.
        run("git", "push", "origin", "HEAD:main")
        log("Feed update and push completed successfully.")
        return 0


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--hours", type=float, default=96,
                            help="rolling import window in hours (default: 96)")
        parser.add_argument("--max-scan", type=int, default=30,
                            help="maximum channel entries to inspect (default: 30)")
        args = parser.parse_args()
        raise SystemExit(publish(args.hours, args.max_scan))
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError, KeyError, ET.ParseError) as error:
        log(f"ERROR: {error}")
        raise SystemExit(1)
