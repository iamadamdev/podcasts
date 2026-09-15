# Custom Video Podcasts

## Update and publish manually

Requires Python 3, `yt-dlp`, `ffmpeg` (including `ffprobe`), and Git with working
GitHub credentials. On macOS, install the download tools with
`brew install yt-dlp ffmpeg`.

From a clean `main` checkout, run:

```sh
python3 scripts/publish_feed.py
```

This pulls `main` with `--ff-only`, runs `python3 scripts/update_mk.py --hours 96`,
commits feed changes when present, and pushes normally. It stages only
`episodes.json`, `feed.xml`, `index.html`, and the MP3s referenced by the manifest.
Overlapping publisher runs are skipped. Uncommitted changes, a different branch,
or diverged Git history stop the run with an error in the logs. Keep this checkout
on `main` and commit or move any unfinished changes before the scheduled time.

The publisher pins both the commit author and committer to **Adam
<36013816+iamadamdev@users.noreply.github.com>**, overriding Git configuration and
inherited identity environment variables. Future Git identity changes will not
change the identity used for automated feed commits.

To update files without committing or pushing:

```sh
python3 scripts/update_mk.py --hours 96
```

To preview new videos without changing the feed:

```sh
python3 scripts/update_mk.py --hours 96 --dry-run
```

## Daily macOS automation

Install or reinstall the background job from this checkout:

```sh
python3 scripts/install_daily_update.py
```

The job runs every day at **10:00am in the Mac's local timezone** (currently
America/Los_Angeles), including daylight saving changes. It stays installed across
reboots and loads when you log in. The installer records this checkout's location
and Python path; rerun it if either changes.

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

A failed update never proceeds to the commit/push steps. Inspect the logs and
resolve any partially written files before retrying. If only the push failed,
the commit remains locally and the next successful run retries the push even
when there are no new episodes. A rejected push is reported without force-pushing.

### Remove the schedule

```sh
launchctl bootout "gui/$(id -u)/com.iamadamdev.podcasts.daily-update"
rm ~/Library/LaunchAgents/com.iamadamdev.podcasts.daily-update.plist
```
