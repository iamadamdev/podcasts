"""Exercise publishing against disposable local repositories, without network access."""

import contextlib
import fcntl
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import publish_feed


UPDATER = '''from pathlib import Path
import json
import sys
assert sys.argv[1:] == ["--hours", "96"]
Path("audio_files").mkdir(exist_ok=True)
Path("audio_files/episode.mp3").write_bytes(b"test audio")
Path("episodes.json").write_text(json.dumps([{"filename": "audio_files/episode.mp3"}]))
Path("feed.xml").write_text("updated feed")
Path("index.html").write_text("updated site")
'''


class PublishFeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.root = base / "checkout"
        self.remote = base / "remote.git"
        self.root.mkdir()
        self.git("init", "--bare", str(self.remote))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Publisher Test")
        self.git("config", "user.email", "publisher@example.test")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/update_mk.py").write_text(UPDATER)
        (self.root / "episodes.json").write_text("[]")
        (self.root / "feed.xml").write_text("original feed")
        (self.root / "index.html").write_text("original site")
        self.git("add", ".")
        self.git("commit", "-m", "Create test feed")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")
        self.initial_head = self.git("rev-parse", "HEAD")
        self.addCleanup(patch.stopall)
        patch.object(publish_feed, "ROOT", self.root).start()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.strip()

    def publish(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return publish_feed.publish()

    def remote_head(self):
        return self.git("--git-dir", str(self.remote), "rev-parse", "refs/heads/main")

    def set_updater(self, content):
        (self.root / "scripts/update_mk.py").write_text(content)
        self.git("add", "scripts/update_mk.py")
        self.git("commit", "-m", "Configure test updater")
        self.git("push", "origin", "main")

    def test_publishes_generated_files_and_skips_empty_commit(self):
        self.assertEqual(self.publish(), 0)
        published = self.git("rev-parse", "HEAD")
        self.assertNotEqual(published, self.initial_head)
        self.assertEqual(self.remote_head(), published)
        self.assertEqual(
            set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()),
            {"audio_files/episode.mp3", "episodes.json", "feed.xml", "index.html"},
        )
        self.assertEqual(self.publish(), 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), published)

    def test_refuses_uncommitted_work(self):
        (self.root / "notes.txt").write_text("unfinished work")
        with self.assertRaisesRegex(RuntimeError, "uncommitted changes"):
            self.publish()
        self.assertEqual(self.remote_head(), self.initial_head)

    def assert_podcast_identity(self):
        identity = self.git("log", "-1", "--format=%an%n%ae%n%cn%n%ce").splitlines()
        self.assertEqual(identity, [
            "Adam", "36013816+iamadamdev@users.noreply.github.com",
            "Adam", "36013816+iamadamdev@users.noreply.github.com",
        ])
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD"))

    def test_pins_author_and_committer_despite_git_config_changes(self):
        for section in ("user", "author", "committer"):
            self.git("config", f"{section}.name", "Different Person")
            self.git("config", f"{section}.email", "different@example.test")
        self.publish()
        self.assert_podcast_identity()

    def test_pins_author_and_committer_despite_environment_changes(self):
        with patch.dict(os.environ, {
            "GIT_AUTHOR_NAME": "Different Author",
            "GIT_AUTHOR_EMAIL": "author@example.test",
            "GIT_COMMITTER_NAME": "Different Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.test",
        }):
            self.publish()
        self.assert_podcast_identity()

    def test_refuses_a_different_branch(self):
        self.git("checkout", "-b", "feature")
        with self.assertRaisesRegex(RuntimeError, "Switch this checkout to main"):
            self.publish()
        self.assertEqual(self.remote_head(), self.initial_head)

    def test_failed_update_is_not_committed_or_pushed(self):
        self.set_updater('from pathlib import Path\nPath("feed.xml").write_text("partial")\nraise SystemExit(7)\n')
        before = self.remote_head()
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.remote_head(), before)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_retries_failed_push_even_without_new_feed_changes(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        pending = self.git("rev-parse", "HEAD")
        self.assertNotEqual(pending, self.initial_head)
        self.assertEqual(self.remote_head(), self.initial_head)
        hook.unlink()
        self.assertEqual(self.publish(), 0)
        self.assertEqual(self.remote_head(), pending)
        self.assertEqual(self.git("rev-parse", "HEAD"), pending)

    def test_does_not_stage_unrelated_files_created_during_update(self):
        self.set_updater(UPDATER + '\nPath("scratch.txt").write_text("unrelated")\n')
        self.publish()
        self.assertEqual(self.git("ls-files", "scratch.txt"), "")
        self.assertEqual((self.root / "scratch.txt").read_text(), "unrelated")

    def test_preserves_unrelated_changes_staged_during_download(self):
        self.set_updater(
            UPDATER + '\nimport subprocess\nPath("scratch.txt").write_text("user work")\n'
            'subprocess.run(["git", "add", "scratch.txt"], check=True)\n'
        )
        self.publish()
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD", "scratch.txt"), "")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "scratch.txt")

    def test_skips_overlapping_publisher(self):
        with (self.root / ".git/publish-feed.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.publish(), 0)
        self.assertEqual(self.remote_head(), self.initial_head)

    def test_diverged_history_preserves_both_sides(self):
        other = self.root.parent / "other"
        self.git("clone", "-b", "main", str(self.remote), str(other))
        self.git("-C", str(other), "config", "user.name", "Other Publisher")
        self.git("-C", str(other), "config", "user.email", "other@example.test")
        (other / "remote-note.txt").write_text("remote work")
        self.git("-C", str(other), "add", ".")
        self.git("-C", str(other), "commit", "-m", "Add remote work")
        self.git("-C", str(other), "push", "origin", "main")
        remote_head = self.remote_head()
        (self.root / "local-note.txt").write_text("local work")
        self.git("add", ".")
        self.git("commit", "-m", "Add local work")
        local_head = self.git("rev-parse", "HEAD")
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
        self.assertEqual(self.remote_head(), remote_head)


if __name__ == "__main__":
    unittest.main()
