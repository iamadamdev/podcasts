#!/usr/bin/env python3
"""Reject pushes to main when RSS enclosures are absent from the pushed commit."""

from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath
import subprocess
import sys
import xml.etree.ElementTree as ET


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def validate_commit(revision: str) -> None:
    entries = json.loads(git("show", f"{revision}:episodes.json"))
    feed = ET.fromstring(git("show", f"{revision}:feed.xml"))
    files = {}
    for record in git("ls-tree", "-r", "-l", "-z", revision).split("\0"):
        if record:
            metadata, filename = record.split("\t", 1)
            mode, kind, _, size = metadata.split()
            files[filename] = (mode, kind, size)
    items = feed.findall("channel/item")
    by_guid = {item.findtext("guid"): item for item in items}
    if len(entries) != len(items) or len(by_guid) != len(items):
        raise RuntimeError("The committed RSS and manifest have different episode counts or duplicate GUIDs.")
    if len({entry["guid"] for entry in entries}) != len(entries):
        raise RuntimeError("The committed manifest has duplicate GUIDs.")
    for entry in entries:
        filename = entry["filename"]
        path = PurePosixPath(filename)
        if path.parent != PurePosixPath("audio_files") or path.suffix != ".mp3":
            raise RuntimeError(f"Unexpected episode audio path: {filename}")
        mode, kind, size = files.get(filename, (None, None, "0"))
        if mode not in {"100644", "100755"} or kind != "blob" or not 0 < int(size) < 100_000_000:
            raise RuntimeError(f"Referenced MP3 is missing, invalid, or too large in the pushed commit: {filename}")
        item = by_guid.get(entry["guid"])
        enclosure = item.find("enclosure") if item is not None else None
        if (enclosure is None or enclosure.get("type") != "audio/mpeg"
                or not enclosure.get("url", "").endswith("/" + filename)
                or int(enclosure.get("length", "0")) != int(size)):
            raise RuntimeError(f"RSS enclosure does not match committed audio: {filename}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision", nargs="?", default="HEAD")
    parser.add_argument("--pre-push", action="store_true")
    args = parser.parse_args()
    if args.pre_push:
        for line in sys.stdin:
            _, local_sha, remote_ref, _ = line.split()
            if remote_ref == "refs/heads/main" and local_sha.strip("0"):
                validate_commit(local_sha)
    else:
        validate_commit(args.revision)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, ET.ParseError, subprocess.CalledProcessError) as error:
        print(f"Cannot publish podcast feed: {error}\n"
              "Use scripts/publish_feed.py to include and validate all referenced audio.", file=sys.stderr)
        raise SystemExit(1)
