"""Reproduce incomplete manual pushes against a disposable Git remote."""

import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from scripts import install_daily_update
from test_publish_feed import PublisherTestCase


class PublicationGuardTests(PublisherTestCase):
    def setUp(self):
        super().setUp()
        sources = Path(__file__).resolve().parents[1] / "scripts"
        for name in ("check_feed_commit.py", "pre-push"):
            shutil.copy2(sources / name, self.root / "scripts" / name)
        (self.root / "feed.xml").write_text("<rss><channel /></rss>")
        self.git("add", "scripts", "feed.xml")
        self.git("commit", "-m", "Install test guard")
        self.git("push", "origin", "main")
        patch.object(install_daily_update, "ROOT", self.root).start()
        install_daily_update.install_publish_guard()

    def seed_feed_only_commit(self):
        (self.root / "audio_files").mkdir(exist_ok=True)
        self.audio = self.root / "audio_files/episode.mp3"
        self.audio.write_bytes(b"test audio")
        (self.root / "episodes.json").write_text(json.dumps([{
            "filename": "audio_files/episode.mp3", "guid": "episode",
        }]))
        (self.root / "feed.xml").write_text(
            '<rss><channel><item><guid>episode</guid><enclosure '
            'url="https://example.test/audio_files/episode.mp3" length="10" '
            'type="audio/mpeg" /></item></channel></rss>'
        )
        self.git("add", "episodes.json", "feed.xml")
        self.git("commit", "-m", "Reproduce missing committed audio")
        return self.git("rev-parse", "HEAD")

    def test_blocks_feed_only_commit_even_if_audio_is_staged_then_allows_complete_commit(self):
        before = self.remote_head()
        self.seed_feed_only_commit()
        for stage_audio in (False, True):
            if stage_audio:
                self.git("add", "audio_files/episode.mp3")
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                self.git("push", "origin", "HEAD:main")
            self.assertIn("Referenced MP3 is missing", caught.exception.stderr)
            self.assertEqual(self.remote_head(), before)
        self.git("commit", "-m", "Include episode audio")
        self.git("push", "origin", "HEAD:main")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD"))

    def test_checks_the_pushed_revision_instead_of_the_working_checkout(self):
        before = self.remote_head()
        incomplete = self.seed_feed_only_commit()
        self.git("add", "audio_files/episode.mp3")
        self.git("commit", "-m", "Include episode audio")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("push", "origin", f"{incomplete}:refs/heads/main")
        self.assertEqual(self.remote_head(), before)
        self.git("push", "origin", "HEAD:main")

    def test_blocks_incorrect_enclosure_size(self):
        before = self.remote_head()
        self.seed_feed_only_commit()
        feed = self.root / "feed.xml"
        feed.write_text(feed.read_text().replace('length="10"', 'length="20"'))
        self.git("add", "audio_files/episode.mp3", "feed.xml")
        self.git("commit", "-m", "Reproduce incorrect enclosure size")
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.git("push", "origin", "HEAD:main")
        self.assertIn("RSS enclosure does not match", caught.exception.stderr)
        self.assertEqual(self.remote_head(), before)

    def test_blocks_symlinked_audio(self):
        before = self.remote_head()
        self.seed_feed_only_commit()
        self.audio.unlink()
        self.audio.symlink_to("../feed.xml")
        self.git("add", "audio_files/episode.mp3")
        self.git("commit", "-m", "Reproduce symlinked episode")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("push", "origin", "HEAD:main")
        self.assertEqual(self.remote_head(), before)

    def test_accepts_an_empty_feed(self):
        (self.root / "index.html").write_text("updated empty site")
        self.git("add", "index.html")
        self.git("commit", "-m", "Update empty site")
        self.git("push", "origin", "HEAD:main")

    def test_guard_installation_is_idempotent(self):
        hook = self.root / ".git/hooks/pre-push"
        target = hook.readlink()
        install_daily_update.install_publish_guard()
        self.assertEqual(hook.readlink(), target)

    def test_existing_custom_hook_is_preserved(self):
        hook = self.root / ".git/hooks/pre-push"
        hook.unlink()
        hook.write_text("#!/bin/sh\necho custom hook\n")
        with self.assertRaisesRegex(RuntimeError, "Existing pre-push hook was preserved"):
            install_daily_update.install_publish_guard()
        self.assertEqual(hook.read_text(), "#!/bin/sh\necho custom hook\n")

    def test_custom_hooks_directory_is_not_modified(self):
        shared = self.root.parent / "shared-hooks"
        shared.mkdir()
        self.git("config", "core.hooksPath", str(shared))
        with self.assertRaisesRegex(RuntimeError, "Custom core.hooksPath was preserved"):
            install_daily_update.install_publish_guard()
        self.assertEqual(list(shared.iterdir()), [])
