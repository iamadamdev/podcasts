#!/usr/bin/env python3
"""Install a macOS launch agent that publishes daily at 9:30am and 4:00pm."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.iamadamdev.podcasts.daily-update"


def install_publish_guard() -> None:
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=ROOT, text=True, stdout=subprocess.PIPE,
    )
    if configured.stdout.strip():
        raise RuntimeError("Custom core.hooksPath was preserved. Chain scripts/pre-push into that configuration.")
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks/pre-push"],
        cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE,
    )
    hook = Path(result.stdout.strip())
    if not hook.is_absolute():
        hook = ROOT / hook
    source = ROOT / "scripts" / "pre-push"
    if hook.exists() or hook.is_symlink():
        if hook.is_symlink() and hook.resolve() == source.resolve():
            return
        raise RuntimeError(f"Existing pre-push hook was preserved: {hook}. Chain scripts/pre-push into it before reinstalling.")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.symlink_to(source)
    print(f"Installed feed publication guard: {hook}")


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
    install_publish_guard()

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
        "StartCalendarInterval": [
            {"Hour": 9, "Minute": 30},
            {"Hour": 16, "Minute": 0},
        ],
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
    print("Scheduled daily at 9:30am and 4:00pm in the Mac's local timezone.")
    print(f"Logs: {logs_dir}")
    print(f"Run now: launchctl kickstart {service}")


if __name__ == "__main__":
    main()
