#!/usr/bin/env python3
"""Dashboard watchdog - restarts workstate-dashboard.py if it goes down.

Checks localhost:7777 every 15 seconds with an HTTP health check.
Uses exponential backoff on repeated failures to avoid spawn loops.
Single-instance via a lock file.

Usage:
    pythonw dashboard-watchdog.py
"""

import http.client
import os
import subprocess
import sys
import time
from pathlib import Path

PORT = 7777
CHECK_INTERVAL = 15
MAX_BACKOFF = 300  # 5 minute cap
TOOLS_DIR = Path(__file__).parent.resolve()
DASHBOARD_SCRIPT = TOOLS_DIR / "workstate-dashboard.py"
LOGO = TOOLS_DIR / "images" / "logo.png"
LOCK_FILE = TOOLS_DIR / ".watchdog.lock"


def is_alive():
    """HTTP-level health check — detects hung dashboards, not just open ports."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
        conn.request("GET", "/api/sessions")
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception:
        return False


def _pythonw():
    """Resolve pythonw.exe so spawned processes never pop a console window."""
    exe = Path(sys.executable)
    pythonw = exe.parent / "pythonw.exe"
    return str(pythonw) if pythonw.exists() else str(exe)


def start_dashboard():
    args = [_pythonw(), str(DASHBOARD_SCRIPT)]
    if LOGO.exists():
        args += ["--logo", str(LOGO)]
    subprocess.Popen(
        args,
        cwd=str(TOOLS_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def acquire_lock():
    """Prevent multiple watchdog instances via a lock file with PID."""
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            # Check if the old process is still running
            os.kill(old_pid, 0)
            return False  # another watchdog is alive
        except (ValueError, OSError):
            pass  # stale lock file
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main():
    if not acquire_lock():
        return  # another watchdog is already running

    consecutive_failures = 0
    try:
        while True:
            if not is_alive():
                # Double-check to avoid false positives
                time.sleep(3)
                if not is_alive():
                    consecutive_failures += 1
                    backoff = min(CHECK_INTERVAL * (2 ** (consecutive_failures - 1)), MAX_BACKOFF)
                    start_dashboard()
                    time.sleep(backoff)
                    continue
            consecutive_failures = 0
            time.sleep(CHECK_INTERVAL)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
