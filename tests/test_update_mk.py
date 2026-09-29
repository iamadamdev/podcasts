"""Exercise discovery timeouts, downloads, and retention of generated feeds."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts import update_mk


NOW = 1_800_000_000
CUTOFF = NOW - 30 * 24 * 3600


class DiscoveryTests(unittest.TestCase):
    def test_stalled_listing_and_metadata_commands_abort_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as temporary:
            downloader = Path(temporary) / "yt-dlp"
            downloader.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
            downloader.chmod(0o755)
            with patch.dict(os.environ, {"PATH": temporary + os.pathsep + os.environ["PATH"]}), patch.object(
                update_mk, "DISCOVERY_TIMEOUT_SECONDS", 0.1
            ):
                for operation, argument in (
                    (update_mk.flat_video_ids, 30),
                    (update_mk.video_metadata, "stalled-video"),
                ):
                    with self.subTest(operation=operation.__name__):
                        with self.assertRaisesRegex(RuntimeError, "timed out after 0.1s"):
                            operation(argument)

    def test_progress_is_visible_before_each_network_request(self):
        output = io.StringIO()

        def listing(max_scan):
            self.assertIn("Scanning up to 30 Meet Kevin videos", output.getvalue())
            return ["newest", "older"]

        def metadata(video_id):
            index = 1 if video_id == "newest" else 2
            self.assertIn(f"Checking video {index}/2: {video_id}", output.getvalue())
            return {
                "id": video_id, "channel_id": update_mk.EXPECTED_CHANNEL_ID,
                "timestamp": NOW if video_id == "newest" else NOW - 60,
            }

        with contextlib.redirect_stdout(output), patch.object(
            update_mk.time, "time", return_value=NOW
        ), patch.object(update_mk, "flat_video_ids", side_effect=listing), patch.object(
            update_mk, "video_metadata", side_effect=metadata
        ):
            videos = update_mk.discover_recent_videos(96, 30)
        self.assertEqual([video["id"] for video in videos], ["older", "newest"])


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name, value in {
            "ROOT": self.root,
            "AUDIO_DIR": self.root / "audio_files",
            "EPISODES_PATH": self.root / "episodes.json",
            "FEED_PATH": self.root / "feed.xml",
            "INDEX_PATH": self.root / "index.html",
        }.items():
            patcher = patch.object(update_mk, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, kwargs in (
            ("time.time", {"return_value": NOW}),
            ("require_tools", {}),
            ("discover_recent_videos", {"return_value": []}),
        ):
            patcher = patch(f"scripts.update_mk.{target}", **kwargs)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if target == "discover_recent_videos":
                self.discover = mock
        (self.root / "audio_files").mkdir()
        (self.root / "index.html").write_text(
            f"<html>\n{update_mk.EPISODES_START}\n{update_mk.EPISODES_END}\n</html>\n"
        )

    def episode(self, name, timestamp, number):
        filename = f"audio_files/{name}.mp3"
        (self.root / filename).write_bytes(b"test audio")
        return {
            "author": "Adam" if name == "old" else "Meet Kevin",
            "title": name,
            "description": f"Description of {name}",
            "duration_seconds": 60,
            "episode_number": number,
            "filename": filename,
            "guid": name,
            "published_timestamp": timestamp,
            "source_id": None if name == "old" else name,
        }

    def seed(self, episodes):
        update_mk.save_episodes(episodes)
        update_mk.rebuild_outputs(episodes)

    def update(self, *args):
        with patch("sys.argv", ["update_mk.py", *args]), contextlib.redirect_stdout(io.StringIO()):
            return update_mk.main()

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_prunes_every_author_without_new_videos_and_keeps_exact_cutoff(self):
        old = self.episode("old", CUTOFF - 1, 1)
        old_kevin = self.episode("old-kevin", CUTOFF - 60, 2)
        boundary = self.episode("boundary", CUTOFF, 3)
        recent = self.episode("recent", NOW, 4)
        self.seed([old, old_kevin, boundary, recent])
        unrelated = self.root / "audio_files/unrelated.mp3"
        unrelated.write_bytes(b"leave alone")

        self.assertEqual(self.update(), 0)
        self.assertEqual(update_mk.load_episodes(), [boundary, recent])
        for episode in (old, old_kevin):
            self.assertFalse((self.root / episode["filename"]).exists())
            for filename in ("feed.xml", "index.html"):
                self.assertNotIn(episode["filename"], (self.root / filename).read_text())
        feed = ET.parse(self.root / "feed.xml")
        self.assertEqual([item.findtext("guid") for item in feed.findall("channel/item")], ["recent", "boundary"])
        for episode in (boundary, recent):
            self.assertTrue((self.root / episode["filename"]).is_file())
            self.assertIn(episode["filename"], (self.root / "index.html").read_text())
        self.assertEqual(unrelated.read_bytes(), b"leave alone")
        before = self.snapshot()
        self.update()
        self.assertEqual(self.snapshot(), before)

    def test_all_episodes_can_expire_and_empty_feed_remains_valid(self):
        self.seed([self.episode("old", CUTOFF - 1, 1)])
        self.update()
        self.assertEqual(update_mk.load_episodes(), [])
        self.assertEqual(list((self.root / "audio_files").iterdir()), [])
        self.assertEqual(ET.parse(self.root / "feed.xml").findall("channel/item"), [])
        self.assertNotIn("<article>", (self.root / "index.html").read_text())
        before = self.snapshot()
        self.update()
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_preserves_expired_audio_and_outputs(self):
        self.seed([self.episode("old", CUTOFF - 1, 1)])
        before = self.snapshot()
        self.assertEqual(self.update("--dry-run"), 0)
        self.assertEqual(self.snapshot(), before)

    def test_wide_import_window_does_not_download_expired_videos(self):
        self.seed([])
        self.discover.return_value = [{"id": "expired-video", "title": "Old video", "timestamp": CUTOFF - 1}]
        with patch.object(update_mk, "download_audio") as download:
            self.update("--hours", "1440")
        download.assert_not_called()
        self.assertEqual(update_mk.load_episodes(), [])

    def test_new_episode_is_imported_as_old_episode_expires(self):
        self.seed([self.episode("old", CUTOFF - 1, 7)])
        self.discover.return_value = [{
            "id": "new", "title": "New episode", "timestamp": NOW,
            "webpage_url": "https://www.youtube.com/watch?v=new",
        }]
        new_audio = Path("audio_files/new.mp3")
        (self.root / new_audio).write_bytes(b"new audio")
        with patch.object(update_mk, "download_audio", return_value=new_audio), patch.object(update_mk, "probe_duration", return_value=60):
            self.update()
        episodes = update_mk.load_episodes()
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["episode_number"], 8)
        self.assertEqual(episodes[0]["source_id"], "new")
        self.assertFalse((self.root / "audio_files/old.mp3").exists())
        self.assertIn("audio_files/new.mp3", (self.root / "feed.xml").read_text())

    def test_failed_discovery_does_not_delete_or_change_existing_episodes(self):
        self.seed([self.episode("old", CUTOFF - 1, 1)])
        self.discover.side_effect = RuntimeError("discovery failed")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "discovery failed"):
            self.update()
        self.assertEqual(self.snapshot(), before)

    def test_invalid_site_does_not_delete_audio_or_change_manifest_and_feed(self):
        self.seed([self.episode("old", CUTOFF - 1, 1)])
        (self.root / "index.html").write_text("missing markers")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "missing episode marker"):
            self.update()
        self.assertEqual(self.snapshot(), before)

    def test_expired_paths_outside_audio_directory_are_rejected(self):
        old = self.episode("old", CUTOFF - 1, 1)
        for filename in ("notes.mp3", "audio_files/../notes.mp3", str(self.root / "notes.mp3")):
            with self.subTest(filename=filename):
                (self.root / "notes.mp3").write_bytes(b"unrelated audio")
                old["filename"] = filename
                self.seed([old])
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, "Unexpected episode audio path"):
                    self.update()
                self.assertEqual(self.snapshot(), before)

    def test_symlinked_expired_audio_is_rejected_before_writing(self):
        old = self.episode("old", CUTOFF - 1, 1)
        target = self.root / "notes.mp3"
        target.write_bytes(b"unrelated audio")
        audio = self.root / old["filename"]
        audio.unlink()
        audio.symlink_to(target)
        self.seed([old])
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Unexpected episode audio path"):
            self.update()
        self.assertTrue(audio.is_symlink())
        self.assertEqual(self.snapshot(), before)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.audio = self.root / "audio_files"
        self.audio.mkdir()
        for name, value in (("ROOT", self.root), ("AUDIO_DIR", self.audio)):
            patcher = patch.object(update_mk, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = patch.object(update_mk.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        self.metadata = {
            "id": "example", "timestamp": NOW, "title": "Test episode",
            "webpage_url": "https://www.youtube.com/watch?v=example",
        }

    def output_path(self, command):
        return Path(command[command.index("--output") + 1].replace("%(ext)s", "mp3"))

    def test_retries_with_clean_audio_after_partial_download_failure(self):
        attempts = []

        def download(command):
            output = self.output_path(command)
            self.assertFalse(output.exists())
            attempts.append(output)
            output.with_suffix(".webm.part").write_bytes(b"partial source")
            if len(attempts) == 1:
                output.write_bytes(b"incomplete audio")
                raise subprocess.CalledProcessError(1, command)
            output.write_bytes(b"complete audio")

        with patch.object(update_mk, "run", side_effect=download), patch.object(update_mk, "probe_duration", return_value=60):
            path = update_mk.download_audio(self.metadata)
        self.assertEqual((self.root / path).read_bytes(), b"complete audio")
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(not path.parent.exists() for path in attempts))
        self.assertEqual(list(self.audio.iterdir()), [self.root / path])

    def test_exhausted_retries_leave_no_audio_or_partial_files(self):
        def fail(command):
            self.output_path(command).with_suffix(".webm.part").write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, command)

        with patch.object(update_mk, "run", side_effect=fail) as downloader:
            with self.assertRaises(subprocess.CalledProcessError):
                update_mk.download_audio(self.metadata)
        self.assertEqual(downloader.call_count, 3)
        self.assertEqual(list(self.audio.iterdir()), [])
        self.assertEqual(self.sleep.call_count, 2)

    def test_invalid_audio_is_not_promoted_or_reused(self):
        def download(command):
            self.output_path(command).write_bytes(b"invalid audio")

        with patch.object(update_mk, "run", side_effect=download), patch.object(
            update_mk, "probe_duration", side_effect=subprocess.CalledProcessError(1, ["ffprobe"])
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                update_mk.download_audio(self.metadata)
        self.assertEqual(list(self.audio.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
