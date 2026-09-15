#!/usr/bin/env python3
"""Install a macOS launch agent that publishes this checkout daily at 10am."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.iamadamdev.podcasts.daily-update"


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This installer requires macOS.")

    # launchd does not read shell startup files, so supply the tools' paths.
    tool_dirs = [str(Path(sys.executable).parent)]
    for name in ("git", "yt-dlp", "ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if executable is None:
            raise SystemExit(f"Missing required tool: {name}")
        tool_dirs.append(str(Path(executable).parent))
    tool_dirs.extend(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    logs_dir = Path.home() / "Library" / "Logs" / "podcasts"
    agents_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / f"{LABEL}.plist"
    config = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-i",
            sys.executable, "-u", str(ROOT / "scripts" / "publish_feed.py"),
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": ":".join(dict.fromkeys(tool_dirs)),
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONUNBUFFERED": "1",
        },
        "StartCalendarInterval": {"Hour": 10, "Minute": 0},
        "StandardOutPath": str(logs_dir / "daily-update.log"),
        "StandardErrorPath": str(logs_dir / "daily-update.error.log"),
        "ProcessType": "Background",
    }
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    existing = subprocess.run(
        ["launchctl", "print", service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if existing.returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)
    plist_path.write_bytes(plistlib.dumps(config))
    subprocess.run(["plutil", "-lint", str(plist_path)], check=True)
    subprocess.run(["launchctl", "enable", service], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    print(f"Installed {plist_path}")
    print("Scheduled daily at 10:00am in the Mac's local timezone.")
    print(f"Logs: {logs_dir}")
    print(f"Run now: launchctl kickstart {service}")


if __name__ == "__main__":
    main()
