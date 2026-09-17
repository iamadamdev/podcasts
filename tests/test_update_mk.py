"""Exercise retention with real manifests, audio files, and generated outputs."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts import update_mk


NOW = 1_800_000_000
CUTOFF = NOW - 30 * 24 * 3600


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


if __name__ == "__main__":
    unittest.main()
