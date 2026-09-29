# Custom Video Podcasts

## Update and publish manually

Requires Python 3, `yt-dlp`, `ffmpeg` (including `ffprobe`), and Git with working
GitHub credentials. On macOS, install the download tools with
`brew install yt-dlp ffmpeg`.

From a clean `main` checkout, run:

```sh
python3 scripts/publish_feed.py
```

This pulls `main` with `--ff-only`, runs `python3 scripts/update_mk.py --hours 96`
in a temporary Git worktree,
removes episodes older than **30 days**, commits feed changes when present, and
pushes normally. The cutoff uses each episode's publication timestamp, applies
to every author, and keeps episodes exactly 30 days old. Expired episodes are
removed from `episodes.json`, `feed.xml`, `index.html`, and `audio_files/`, even
when no new videos are found. An empty feed is supported if all episodes expire.
It stages only the manifest, feed, site, and MP3s referenced before or after the
update, so audio deletions are included in the commit and push. Deleted audio
remains in earlier Git history.
Only a successful update is committed and fast-forwarded into this checkout.
Downloads and partial outputs from a failed update are discarded with the temporary
worktree, so they cannot block the next scheduled run. Download attempts check for
working formats and retry up to three times with fresh media URLs; incomplete
audio is never reused as a finished episode.

If a feed-only commit omitted an MP3 that still exists locally, the publisher
automatically recovers it. This exception applies only to untracked MP3s already
referenced by the unchanged, committed manifest and RSS feed. Recovery verifies
the enclosure size, episode duration, and full audio decoding, preserves the file
in `.git/publish-audio-recovery/`, and commits and pushes the repaired audio before
attempting new downloads. Failed recovery attempts keep that private copy for
the next run. Every normal publish also checks that all referenced audio exists
and is staged in Git before committing.

Overlapping publisher runs are skipped. Unrelated untracked files, tracked or
staged edits, a different branch, or diverged Git history stop the run with an
error in the logs. Keep this checkout on `main` and commit or move any unfinished
changes before the scheduled time. Use `scripts/publish_feed.py` for manual
publishing too; committing only the feed files can omit new MP3s.

The daily-job installer also installs `scripts/pre-push` as this checkout's Git
pre-push hook. It checks the actual commit being pushed to `main`, rejecting
missing or symlinked audio, invalid enclosure sizes, and manifest/RSS mismatches.
Having an MP3 only in the working directory or staging area is insufficient.
Existing custom hooks are preserved; the installer asks that you chain the guard
into them. Install the job in each new checkout to enable this local guard there.
You can run the same check manually with `python3 scripts/check_feed_commit.py`.

To catch up after an outage longer than the normal four-day import window:

```sh
python3 scripts/publish_feed.py --hours 168 --max-scan 100
```

This scans up to 100 videos for the last seven days, still retaining only 30 days
of episodes. The daily schedule continues to use its normal 96-hour window.

The publisher pins both the commit author and committer to **Adam
<36013816+iamadamdev@users.noreply.github.com>**, overriding Git configuration and
inherited identity environment variables. Future Git identity changes will not
change the identity used for automated feed commits.

To import recent videos and remove expired episodes without committing or pushing:

```sh
python3 scripts/update_mk.py --hours 96
```

To preview new videos and expired episodes without changing the feed:

```sh
python3 scripts/update_mk.py --hours 96 --dry-run
```

The preview still checks YouTube for each video's exact publication time, which
can take a minute or more. It prints progress before listing the channel and
before checking each video. Discovery requests use a 30-second socket timeout,
and each channel listing or video metadata command has a two-minute total limit.
Exceeding the total limit stops the update before any feed files or audio are changed.

## Daily macOS automation

Install or reinstall the background job from this checkout:

```sh
python3 scripts/install_daily_update.py
```

The job runs every day at **10:00am in the Mac's local timezone** (currently
America/Los_Angeles), including daylight saving changes. It stays installed across
reboots and loads when you log in. The installer records this checkout's location
and Python path; rerun it if either changes.
The existing job automatically uses the 30-day cleanup; no reinstall is needed.

The screen can be locked and Terminal can be closed. You must remain logged in,
have network access, and have Git credentials available without interaction.
`caffeinate` prevents idle sleep during an active update. If the Mac is already
asleep at 10am, [launchd runs the job after wake](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html).
It cannot run while the Mac is shut down, and shutting down at the scheduled time
does not create a catch-up run. For a 10am run, leave the Mac awake and online.

### Run now or check status

```sh
launchctl kickstart "gui/$(id -u)/com.iamadamdev.podcasts.daily-update"
launchctl print "gui/$(id -u)/com.iamadamdev.podcasts.daily-update"
tail -n 50 ~/Library/Logs/podcasts/daily-update.log
tail -n 50 ~/Library/Logs/podcasts/daily-update.error.log
```

The job label is `com.iamadamdev.podcasts.daily-update`. The plist lives at
`~/Library/LaunchAgents/com.iamadamdev.podcasts.daily-update.plist`.

New downloads and feed updates are committed only after the updater succeeds.
Failed attempts leave this checkout unchanged apart from the initial fast-forward
pull and any omitted-audio repair. Repairs are committed and pushed separately
before the update, so they remain published even if a subsequent download fails.
Inspect the logs before retrying.
Manual runs of `scripts/update_mk.py` still write directly to the current checkout;
use `scripts/publish_feed.py` for failure isolation. If only the push failed,
the commit remains locally and the next successful run retries the push even
when there are no new episodes. A rejected push is reported without force-pushing.

### Remove the schedule

```sh
launchctl bootout "gui/$(id -u)/com.iamadamdev.podcasts.daily-update"
rm ~/Library/LaunchAgents/com.iamadamdev.podcasts.daily-update.plist
```
