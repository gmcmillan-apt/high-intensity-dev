#!/usr/bin/env python3
"""Workstate Dashboard - local multi-session status aggregator.

A tiny web server that multiple Claude Code sessions POST their status to.
One browser tab shows everything at a glance: sessions, subagents, staleness.

Zero dependencies - stdlib only.

Usage:
    python workstate-dashboard.py [--port PORT]

Default port: 7777
Dashboard: http://localhost:7777
"""

import argparse
import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

# Set at startup by --logo/--logo-left/--logo-right flags
LOGO_DATA_URI = ""       # header logo (--logo)
GUS_LOGO_URI = ""        # header right logo (gusai_logo.png)
LOGO_LEFT_URI = ""       # bottom-left watermark (--logo-left)
LOGO_RIGHT_URI = ""      # bottom-right watermark (--logo-right)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Thread:
    thread_id: str
    name: str
    task: str
    status: str
    risk: str
    started: str
    last_seen: str


@dataclass
class Session:
    session_id: str
    name: str
    task: str
    status: str
    risk: str
    started: str
    last_seen: str
    tab: str = ""
    usage: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    threads: dict = field(default_factory=dict)  # thread_id -> Thread


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

sessions: dict[str, Session] = {}
expired: list[dict] = []
lock = threading.Lock()

MAX_HISTORY = 5
WARN_SECONDS = 60
STALE_SECONDS = 180
EXPIRE_SECONDS = 600
PURGE_SECONDS = 300


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def seconds_since(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 0


def staleness(last_seen):
    age = seconds_since(last_seen)
    if age < WARN_SECONDS:
        return "ok"
    elif age < STALE_SECONDS:
        return "warning"
    else:
        # Sessions stay visible as "idle" — they never auto-expire.
        # Only explicit Done/Delete removes a session.
        return "idle"


def relative_time(iso_str):
    age = seconds_since(iso_str)
    if age < 60:
        return f"{int(age)}s ago"
    elif age < 3600:
        return f"{int(age // 60)}m ago"
    else:
        return f"{int(age // 3600)}h ago"


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def upsert_session(data):
    sid = data.get("session_id")
    if not sid:
        return {"error": "Missing required field: session_id"}, 400

    parent_id = data.get("parent_id")

    with lock:
        # Thread update (child of a session)
        if parent_id and parent_id in sessions:
            parent = sessions[parent_id]
            tid = data.get("thread_id", sid)
            if tid in parent.threads:
                t = parent.threads[tid]
                t.task = data.get("task", t.task)
                t.status = data.get("status", t.status)
                t.risk = data.get("risk", t.risk)
                t.last_seen = now_iso()
            else:
                parent.threads[tid] = Thread(
                    thread_id=tid,
                    name=data.get("name", tid),
                    task=data.get("task", ""),
                    status=data.get("status", "Running"),
                    risk=data.get("risk", "-"),
                    started=now_iso(),
                    last_seen=now_iso(),
                )
            # Clean up done threads
            done = [k for k, v in parent.threads.items() if v.status == "Done"]
            for k in done:
                del parent.threads[k]
            return {"ok": True, "session_id": parent_id, "thread_id": tid,
                    "active_sessions": len(sessions)}, 200

        # Session update
        if sid in sessions:
            s = sessions[sid]
            new_task = data.get("task", s.task)
            if new_task != s.task:
                s.history.append(s.task)
                if len(s.history) > MAX_HISTORY:
                    s.history = s.history[-MAX_HISTORY:]
            s.task = new_task
            s.status = data.get("status", s.status)
            s.risk = data.get("risk", s.risk)
            s.last_seen = now_iso()
        else:
            sessions[sid] = Session(
                session_id=sid,
                name=data.get("name", f"session-{len(sessions) + 1}"),
                task=data.get("task", "Session started"),
                status=data.get("status", "Running"),
                risk=data.get("risk", "-"),
                started=now_iso(),
                last_seen=now_iso(),
            )

        # Remove session if Done
        if sessions.get(sid) and sessions[sid].status == "Done":
            s = sessions.pop(sid)
            expired.append({
                "name": s.name,
                "last_task": s.task,
                "expired_at": now_iso(),
            })

        return {"ok": True, "session_id": sid,
                "name": sessions[sid].name if sid in sessions else data.get("name", ""),
                "active_sessions": len(sessions)}, 200


def delete_session(sid):
    with lock:
        if sid in sessions:
            s = sessions.pop(sid)
            expired.append({
                "name": s.name,
                "last_task": s.task,
                "expired_at": now_iso(),
            })
            return {"ok": True, "removed": sid}, 200
        return {"error": "Session not found"}, 404


def get_sessions_json():
    with lock:
        result = []
        for s in sessions.values():
            threads = []
            for t in s.threads.values():
                threads.append({
                    "thread_id": t.thread_id,
                    "name": t.name,
                    "task": t.task,
                    "status": t.status,
                    "risk": t.risk,
                    "started": t.started,
                    "last_seen": t.last_seen,
                    "staleness": staleness(t.last_seen),
                    "last_seen_relative": relative_time(t.last_seen),
                })
            result.append({
                "session_id": s.session_id,
                "name": s.name,
                "task": s.task,
                "status": s.status,
                "risk": s.risk,
                "tab": s.tab,
                "usage": s.usage,
                "started": s.started,
                "last_seen": s.last_seen,
                "staleness": staleness(s.last_seen),
                "last_seen_relative": relative_time(s.last_seen),
                "history": list(s.history),
                "threads": threads,
            })

        total_threads = sum(len(s.threads) for s in sessions.values())
        # Aggregate usage across all sessions
        agg_usage = {"input_tokens": 0, "output_tokens": 0,
                     "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0}
        for s in sessions.values():
            for k in agg_usage:
                agg_usage[k] += s.usage.get(k, 0)
        agg_usage["cost_usd"] = round(agg_usage["cost_usd"], 4)

        # System stats (cached 10s)
        now = time.time()
        if now - _system_stats_cache["ts"] > 10:
            _system_stats_cache["data"] = _get_system_stats()
            _system_stats_cache["ts"] = now
        sys_stats = _system_stats_cache["data"]

        # Railway stats (cached 30s)
        if now - _railway_cache["ts"] > 30:
            _railway_cache["data"] = _get_railway_stats()
            _railway_cache["ts"] = now
        railway_stats = _railway_cache["data"]

        return {
            "sessions": result,
            "expired": list(expired[-10:]),
            "counts": {
                "sessions": len(sessions),
                "threads": total_threads,
            },
            "usage": agg_usage,
            "system": sys_stats,
            "railway": railway_stats,
            "timestamp": now_iso(),
        }


# ---------------------------------------------------------------------------
# Expiry sweeper
# ---------------------------------------------------------------------------

def sweeper():
    while True:
        time.sleep(30)
        with lock:
            # Sessions NEVER auto-expire. Only explicit Done/Delete removes them.
            # An idle session just means the user hasn't talked to that tab yet.

            # Threads (subagents) DO auto-expire after EXPIRE_SECONDS —
            # a silent subagent is probably dead.
            for sid, s in sessions.items():
                to_remove = []
                for tid, t in s.threads.items():
                    if seconds_since(t.last_seen) > EXPIRE_SECONDS:
                        to_remove.append(tid)
                for tid in to_remove:
                    del s.threads[tid]

            # Purge old expired entries (from Done/Delete) after PURGE_SECONDS
            cutoff = PURGE_SECONDS
            expired[:] = [
                e for e in expired
                if seconds_since(e.get("expired_at", now_iso())) < cutoff
            ]


# ---------------------------------------------------------------------------
# Claude Code session auto-detection
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
ACTIVE_THRESHOLD_SEC = 300  # sessions modified in last 5 min = active
IDLE_THRESHOLD_SEC = 7200   # sessions modified in last 2 hours = idle
AUTO_PREFIX = "auto-cc-"    # prefix for auto-detected session IDs

def _get_boot_time() -> float:
    """Return system boot timestamp (epoch seconds). Uses kernel32 on Windows."""
    try:
        import ctypes
        uptime_ms = ctypes.windll.kernel32.GetTickCount64()
        return time.time() - uptime_ms / 1000.0
    except Exception:
        return 0.0  # fallback: don't filter

SYSTEM_BOOT_TIME = _get_boot_time()


def _count_claude_processes() -> int:
    """Count running claude.exe processes."""
    try:
        import subprocess
        r = subprocess.run(
            ["wmic", "process", "where", "name='claude.exe'", "get",
             "ProcessId", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=5, errors="replace",
        )
        count = 0
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if parts and parts[-1].isdigit():
                count += 1
        return count
    except Exception:
        return -1  # unknown — don't cap


def _project_label(dirname: str) -> str:
    """Convert project dir name to a short label.
    C--Users-gmcmillan-Desktop-AI-Projects-ACV-AI-Agent-gus-demo-r1 -> gus-demo-r1
    """
    parts = dirname.replace("C--", "").replace("c--", "").split("-")
    # Take the last meaningful segments
    # Find last segment that looks like a project name (skip Users, gmcmillan, Desktop, etc)
    skip = {"users", "gmcmillan", "desktop", "ai", "projects", "acv", "agent"}
    meaningful = [p for p in parts if p.lower() not in skip]
    if meaningful:
        return "-".join(meaningful[-3:])  # last 3 segments
    return dirname[-30:]


def _extract_last_user_message(jsonl_path: Path) -> str:
    """Read the last user message from a JSONL transcript (read from end for speed)."""
    try:
        # Read last 200KB to find the last user message
        size = jsonl_path.stat().st_size
        read_bytes = min(size, 200_000)
        with open(jsonl_path, "rb") as f:
            if size > read_bytes:
                f.seek(size - read_bytes)
            chunk = f.read().decode("utf-8", errors="replace")

        last_msg = ""
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "user":
                continue
            msg = d.get("message", {})
            content = ""
            if isinstance(msg, dict):
                c = msg.get("content", "")
                if isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            # Skip system reminders
                            if text and not text.startswith("<system-reminder>"):
                                content = text
                                break
                elif isinstance(c, str):
                    content = c.strip()
            elif isinstance(msg, str):
                content = msg.strip()
            if content:
                last_msg = content
        return last_msg[:120] if last_msg else "Session active"
    except Exception:
        return "Session active"


def _extract_first_user_message(jsonl_path: Path) -> str:
    """Read the first user message from a JSONL transcript."""
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user":
                    continue
                msg = d.get("message", {})
                content = ""
                if isinstance(msg, dict):
                    c = msg.get("content", "")
                    if isinstance(c, list):
                        for block in c:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "").strip()
                                if text and not text.startswith("<system-reminder>"):
                                    content = text
                                    break
                    elif isinstance(c, str):
                        content = c.strip()
                elif isinstance(msg, str):
                    content = msg.strip()
                if content:
                    return content[:80]
        return "Claude Code"
    except Exception:
        return "Claude Code"


def _extract_slug(jsonl_path: Path) -> str:
    """Extract the slug field from a subagent JSONL (scan first ~20 lines)."""
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                slug = d.get("slug")
                if slug:
                    return slug
    except Exception:
        pass
    return ""


# Model pricing per million tokens (USD)
MODEL_PRICING = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
}
_DEFAULT_PRICING = {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50}


def _extract_usage(jsonl_path: Path) -> dict:
    """Sum token usage and estimate cost from a JSONL session file."""
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0}
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = d.get("message", {})
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if not usage or not isinstance(usage, dict):
                    continue
                model = msg.get("model", "") if isinstance(msg, dict) else ""
                pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
                inp = usage.get("input_tokens", 0) or 0
                out = usage.get("output_tokens", 0) or 0
                cw = usage.get("cache_creation_input_tokens", 0) or 0
                cr = usage.get("cache_read_input_tokens", 0) or 0
                totals["input_tokens"] += inp
                totals["output_tokens"] += out
                totals["cache_write_tokens"] += cw
                totals["cache_read_tokens"] += cr
                totals["cost_usd"] += (
                    inp * pricing["input"] / 1_000_000
                    + out * pricing["output"] / 1_000_000
                    + cw * pricing["cache_write"] / 1_000_000
                    + cr * pricing["cache_read"] / 1_000_000
                )
    except Exception:
        pass
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return totals


def _get_system_stats() -> dict:
    """Get CPU, memory, and disk usage."""
    import shutil
    import subprocess
    stats = {"cpu_pct": 0, "mem_pct": 0, "mem_used_gb": 0, "mem_total_gb": 0,
             "disk_pct": 0, "disk_used_gb": 0, "disk_total_gb": 0}
    # Disk via stdlib (always works)
    try:
        du = shutil.disk_usage("C:\\")
        used = du.total - du.free
        stats["disk_pct"] = round(used / du.total * 100, 1)
        stats["disk_used_gb"] = round(used / (1024**3))
        stats["disk_total_gb"] = round(du.total / (1024**3))
    except Exception:
        pass
    # CPU + Memory via single PowerShell call (reliable JSON output)
    try:
        ps_cmd = (
            "$os = Get-CimInstance Win32_OperatingSystem;"
            "$cpu = (Get-CimInstance Win32_Processor).LoadPercentage;"
            "$tj = @{cpu=[int]$cpu;"
            "mem_free=[math]::Round($os.FreePhysicalMemory/1048576,1);"
            "mem_total=[math]::Round($os.TotalVisibleMemorySize/1048576,1)};"
            "ConvertTo-Json $tj -Compress"
        )
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=8, errors="replace",
        )
        if r.stdout.strip():
            d = json.loads(r.stdout.strip())
            stats["cpu_pct"] = d.get("cpu", 0) or 0
            mem_free = d.get("mem_free", 0) or 0
            mem_total = d.get("mem_total", 0) or 0
            if mem_total > 0:
                mem_used = round(mem_total - mem_free, 1)
                stats["mem_pct"] = round(mem_used / mem_total * 100, 1)
                stats["mem_used_gb"] = mem_used
                stats["mem_total_gb"] = mem_total
    except Exception:
        pass
    return stats

# Cache system stats (refresh every 10s to avoid hammering wmic)
_system_stats_cache = {"data": {}, "ts": 0}

# Railway integration
RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "cd22118e-5590-4599-972a-0f74a1c746d9")
RAILWAY_PROJECT_ID = "8798273c-5bcf-4be7-8111-959295307ada"
RAILWAY_ENV_ID = "26f2c503-c4f0-4801-a4e5-5b14c0eac065"
RAILWAY_SERVICES = {
    "backend": "c22cb112-642f-4632-83f0-948c2f68d5bc",
    "frontend": "0174e9bf-b6c5-4990-aa12-3fa42ff4f268",
}
RAILWAY_API = "https://backboard.railway.com/graphql/v2"
_railway_cache = {"data": {}, "ts": 0}


def _get_railway_stats() -> dict:
    """Fetch Railway service metrics + estimated usage via GraphQL API."""
    import subprocess
    from urllib.request import Request, urlopen
    stats = {"backend": {"cpu": 0, "mem_mb": 0}, "frontend": {"cpu": 0, "mem_mb": 0},
             "estimated": {"cpu_hrs": 0, "mem_gb_hrs": 0, "net_tx_gb": 0}}
    if not RAILWAY_TOKEN:
        return stats
    headers = {"Content-Type": "application/json", "Project-Access-Token": RAILWAY_TOKEN,
                "User-Agent": "workstate-dashboard/1.0"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    try:
        # Build a single query for both services + estimated usage
        parts = []
        for name, sid in RAILWAY_SERVICES.items():
            parts.append(
                f'{name}: metrics(projectId: "{RAILWAY_PROJECT_ID}", '
                f'environmentId: "{RAILWAY_ENV_ID}", serviceId: "{sid}", '
                f'measurements: [CPU_USAGE, MEMORY_USAGE_GB], '
                f'sampleRateSeconds: 3600, startDate: "{today}") '
                f'{{ measurement values {{ ts value }} }}'
            )
        parts.append(
            f'estimated: estimatedUsage(projectId: "{RAILWAY_PROJECT_ID}", '
            f'measurements: [CPU_USAGE, MEMORY_USAGE_GB, NETWORK_TX_GB]) '
            f'{{ measurement estimatedValue }}'
        )
        query = "{ " + " ".join(parts) + " }"
        body = json.dumps({"query": query}).encode()
        req = Request(RAILWAY_API, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        d = data.get("data", {})
        # Parse service metrics (latest value)
        for name in RAILWAY_SERVICES:
            for m in d.get(name, []):
                vals = m.get("values", [])
                if not vals:
                    continue
                v = vals[-1]["value"]
                if m["measurement"] == "CPU_USAGE":
                    stats[name]["cpu"] = round(v * 100, 2)  # vCPU fraction -> %
                elif m["measurement"] == "MEMORY_USAGE_GB":
                    stats[name]["mem_mb"] = round(v * 1024, 1)
        # Parse estimated usage
        for m in d.get("estimated", []):
            ev = m.get("estimatedValue", 0)
            if m["measurement"] == "CPU_USAGE":
                stats["estimated"]["cpu_hrs"] = round(ev, 1)
            elif m["measurement"] == "MEMORY_USAGE_GB":
                stats["estimated"]["mem_gb_hrs"] = round(ev, 1)
            elif m["measurement"] == "NETWORK_TX_GB":
                stats["estimated"]["net_tx_gb"] = round(ev, 2)
    except Exception:
        pass
    return stats


def _scan_wt_tabs():
    """Get WT tab names + build claude_creation_ts -> tab_name mapping."""
    import subprocess

    # Step 1: Tab names via UI Automation
    ps_cmd = (
        'Add-Type -AssemblyName UIAutomationClient;'
        '$root=[System.Windows.Automation.AutomationElement]::RootElement;'
        '$wt=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,'
        '(New-Object System.Windows.Automation.PropertyCondition('
        '[System.Windows.Automation.AutomationElement]::ClassNameProperty,'
        '"CASCADIA_HOSTING_WINDOW_CLASS")));'
        'if($wt){'
        '$c=New-Object System.Windows.Automation.PropertyCondition('
        '[System.Windows.Automation.AutomationElement]::ControlTypeProperty,'
        '[System.Windows.Automation.ControlType]::TabItem);'
        '$tabs=$wt.FindAll([System.Windows.Automation.TreeScope]::Descendants,$c);'
        '$r=@();foreach($t in $tabs){$r+=$t.Current.Name};'
        'ConvertTo-Json @($r) -Compress}'
    )
    tab_names = []
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15, errors="replace",
        )
        if r.stdout.strip():
            tab_names = json.loads(r.stdout.strip())
            if isinstance(tab_names, str):
                tab_names = [tab_names]
    except Exception:
        pass

    # Step 2: pwsh PIDs by creation time (= tab order), children of WT
    pwsh_ordered = []  # [(pid, wmic_creation_str)]
    wt_pid = None
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='WindowsTerminal.exe'", "get",
             "ProcessId", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=5, errors="replace",
        )
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if parts and parts[-1].isdigit():
                wt_pid = int(parts[-1])
                break
    except Exception:
        pass

    if wt_pid:
        try:
            r = subprocess.run(
                ["wmic", "process", "where",
                 f"name='pwsh.exe' and ParentProcessId={wt_pid}",
                 "get", "ProcessId,CreationDate", "/FORMAT:CSV"],
                capture_output=True, text=True, timeout=10, errors="replace",
            )
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if len(parts) >= 2 and parts[-1].isdigit():
                    pid = int(parts[-1])
                    created = parts[-2] if len(parts) >= 3 else ""
                    pwsh_ordered.append((pid, created))
            pwsh_ordered.sort(key=lambda x: x[1])
        except Exception:
            pass

    # Step 3: Map pwsh PID -> (tab_index, tab_name)
    # Only map the first N pwsh procs where N = number of visible tabs.
    # Extra stale pwsh procs (closed tabs) get skipped.
    pid_to_tab = {}
    for i, (pid, _) in enumerate(pwsh_ordered):
        if i < len(tab_names):
            pid_to_tab[pid] = (i, tab_names[i])

    # Step 4: claude.exe -> parent pwsh -> (tab_index, tab_name)
    # Also grab claude CreationDate for JSONL matching
    claude_info = []  # [(claude_pid, creation_epoch, tab_idx, tab_name)]
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='claude.exe'", "get",
             "ProcessId,ParentProcessId,CreationDate", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=10, errors="replace",
        )
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) >= 3 and parts[-1].isdigit():
                cpid = int(parts[-1])
                ppid = int(parts[-2]) if parts[-2].isdigit() else 0
                cdate = parts[-3] if len(parts) >= 4 else ""
                if ppid in pid_to_tab:
                    idx, name = pid_to_tab[ppid]
                    # Parse wmic date: 20260303094526.904371-360
                    epoch = 0
                    try:
                        dt_str = cdate.split(".")[0]
                        dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
                        epoch = dt.timestamp()
                    except Exception:
                        pass
                    claude_info.append((cpid, epoch, idx, name))
    except Exception:
        pass

    return claude_info


def scan_claude_sessions():
    """Scan ~/.claude/projects/ for active Claude Code sessions."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return

    now_ts = time.time()
    detected = {}
    detected_threads = {}  # parent_sid -> {agent_id -> thread_info}
    claude_info = _scan_wt_tabs()  # [(claude_pid, creation_epoch, tab_idx, tab_name)]

    try:
        for proj_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            project_label = _project_label(proj_dir.name)

            # Pass 1: scan top-level JSONL files (parent sessions)
            for jsonl in proj_dir.glob("*.jsonl"):
                try:
                    mtime = jsonl.stat().st_mtime
                    age = now_ts - mtime
                    if age > IDLE_THRESHOLD_SEC:
                        continue
                    # Skip files last modified before this OS boot (stale from prior session)
                    if SYSTEM_BOOT_TIME and mtime < SYSTEM_BOOT_TIME:
                        continue

                    sid = AUTO_PREFIX + jsonl.stem
                    status = "Running" if age < ACTIVE_THRESHOLD_SEC else "Idle"
                    last_msg = _extract_last_user_message(jsonl)
                    first_msg = _extract_first_user_message(jsonl)

                    # Use first message as session name, last message as current task
                    name = first_msg if first_msg != "Claude Code" else project_label
                    name = f"[{project_label}] {name}"

                    # Match JSONL to WT tab: find the latest claude.exe
                    # that started BEFORE this JSONL was created (one claude
                    # per tab persists across conversations).
                    tab_label = ""
                    try:
                        jctime = jsonl.stat().st_ctime  # Windows = birth time
                        # claude_info sorted by creation epoch
                        ci_sorted = sorted(claude_info, key=lambda x: x[1])
                        for _cpid, cepoch, tidx, tname in ci_sorted:
                            if cepoch <= jctime + 5:  # 5s grace
                                tab_label = f"Tab {tidx}: {tname}"
                    except Exception:
                        pass

                    usage = _extract_usage(jsonl)

                    detected[sid] = {
                        "session_id": sid,
                        "name": name,
                        "task": last_msg,
                        "status": status,
                        "risk": "-",
                        "tab": tab_label,
                        "usage": usage,
                        "mtime_iso": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    }
                except Exception:
                    continue

            # Pass 2: scan subagent JSONL files (nested under parent UUID dirs)
            for sub_dir in proj_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                subagents_dir = sub_dir / "subagents"
                if not subagents_dir.is_dir():
                    continue

                parent_uuid = sub_dir.name
                parent_sid = AUTO_PREFIX + parent_uuid

                for agent_jsonl in subagents_dir.glob("agent-*.jsonl"):
                    try:
                        mtime = agent_jsonl.stat().st_mtime
                        age = now_ts - mtime
                        if age > IDLE_THRESHOLD_SEC:
                            continue
                        # Skip files last modified before this OS boot
                        if SYSTEM_BOOT_TIME and mtime < SYSTEM_BOOT_TIME:
                            continue

                        agent_id = agent_jsonl.stem  # e.g. "agent-a34fbb0c3a222503e"
                        slug = _extract_slug(agent_jsonl)
                        thread_name = slug if slug else agent_id
                        task = _extract_last_user_message(agent_jsonl)
                        status = "Running" if age < ACTIVE_THRESHOLD_SEC else "Idle"
                        mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

                        if parent_sid not in detected_threads:
                            detected_threads[parent_sid] = {}
                        detected_threads[parent_sid][agent_id] = {
                            "thread_id": agent_id,
                            "name": thread_name,
                            "task": task,
                            "status": status,
                            "risk": "-",
                            "mtime_iso": mtime_iso,
                        }
                    except Exception:
                        continue
    except Exception:
        return

    # Cap detected sessions to the number of running claude.exe processes.
    # After a reboot, stale JSONL files may have post-boot mtimes (from
    # session init) but no live process — this prevents ghost sessions.
    n_procs = _count_claude_processes()
    if n_procs >= 0 and len(detected) > n_procs:
        # Keep only the N most recently modified sessions
        ranked = sorted(detected.items(),
                        key=lambda kv: kv[1].get("mtime_iso", ""), reverse=True)
        detected = dict(ranked[:max(n_procs, 0)])

    with lock:
        # Update or create auto-detected sessions
        for sid, info in detected.items():
            if sid in sessions:
                s = sessions[sid]
                new_task = info["task"]
                if new_task != s.task:
                    s.history.append(s.task)
                    if len(s.history) > MAX_HISTORY:
                        s.history = s.history[-MAX_HISTORY:]
                s.task = new_task
                s.status = info["status"]
                s.tab = info.get("tab", s.tab)
                s.usage = info.get("usage", {})
                s.last_seen = info["mtime_iso"]
            else:
                sessions[sid] = Session(
                    session_id=sid,
                    name=info["name"],
                    task=info["task"],
                    status=info["status"],
                    risk=info["risk"],
                    started=info["mtime_iso"],
                    last_seen=info["mtime_iso"],
                    tab=info.get("tab", ""),
                    usage=info.get("usage", {}),
                )

        # Apply detected subagent threads to their parent sessions
        for parent_sid, threads in detected_threads.items():
            if parent_sid not in sessions:
                continue
            parent = sessions[parent_sid]
            for tid, tinfo in threads.items():
                if tid in parent.threads:
                    t = parent.threads[tid]
                    t.task = tinfo["task"]
                    t.status = tinfo["status"]
                    t.last_seen = tinfo["mtime_iso"]
                else:
                    parent.threads[tid] = Thread(
                        thread_id=tid,
                        name=tinfo["name"],
                        task=tinfo["task"],
                        status=tinfo["status"],
                        risk=tinfo["risk"],
                        started=tinfo["mtime_iso"],
                        last_seen=tinfo["mtime_iso"],
                    )
            # Remove auto-detected threads no longer in this scan
            auto_tids = set(threads.keys())
            stale = [t for t in parent.threads if t.startswith("agent-") and t not in auto_tids]
            for t in stale:
                del parent.threads[t]

        # Clean up subagent threads from sessions with no detected subagents
        for sid in sessions:
            if sid.startswith(AUTO_PREFIX) and sid not in detected_threads:
                stale = [t for t in sessions[sid].threads if t.startswith("agent-")]
                for t in stale:
                    del sessions[sid].threads[t]

        # Remove auto-detected sessions that are no longer active
        to_remove = []
        for sid in sessions:
            if sid.startswith(AUTO_PREFIX) and sid not in detected:
                to_remove.append(sid)
        for sid in to_remove:
            s = sessions.pop(sid)
            expired.append({
                "name": s.name,
                "last_task": s.task,
                "expired_at": now_iso(),
            })


def session_scanner():
    """Background thread that periodically scans for Claude Code sessions."""
    while True:
        scan_claude_sessions()
        time.sleep(10)  # scan every 10 seconds


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._serve_html()
        elif path == "/api/sessions":
            self._json_response(get_sessions_json(), 200)
        elif path == "/api/launch-pwsh":
            import subprocess
            subprocess.Popen(["wt", "new-tab", "pwsh"], creationflags=0x00000008)
            self._json_response({"ok": True}, 200)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/session":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except (json.JSONDecodeError, ValueError):
                self._json_response({"error": "Invalid JSON"}, 400)
                return
            result, code = upsert_session(body)
            self._json_response(result, code)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/session/"):
            sid = path[len("/api/session/"):]
            result, code = delete_session(sid)
            self._json_response(result, code)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _json_response(self, data, code):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_html(self):
        html = DASHBOARD_HTML.replace("{{LOGO_DATA_URI}}", LOGO_DATA_URI)
        html = html.replace("{{LOGO_DISPLAY}}", "block" if LOGO_DATA_URI else "none")
        html = html.replace("{{GUS_LOGO_URI}}", GUS_LOGO_URI)
        html = html.replace("{{GUS_LOGO_DISPLAY}}", "block" if GUS_LOGO_URI else "none")
        html = html.replace("{{LOGO_LEFT_URI}}", LOGO_LEFT_URI)
        html = html.replace("{{LOGO_LEFT_DISPLAY}}", "block" if LOGO_LEFT_URI else "none")
        html = html.replace("{{LOGO_RIGHT_URI}}", LOGO_RIGHT_URI)
        html = html.replace("{{LOGO_RIGHT_DISPLAY}}", "block" if LOGO_RIGHT_URI else "none")
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress access logs


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workstate Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23000'/><text x='16' y='22' text-anchor='middle' font-family='monospace' font-weight='bold' font-size='18' fill='%23e34' letter-spacing='-1'>W</text><rect x='2' y='26' width='28' height='3' rx='1' fill='%23e34'/></svg>">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #000000;
    color: #c9d1d9;
    font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 14px;
    padding: 24px;
    min-height: 100vh;
  }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid #21262d;
    position: relative;
  }
  .header-center {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    text-align: center;
  }
  .header h1 {
    font-size: 18px;
    font-weight: 600;
    color: #e6edf3;
    letter-spacing: 0.5px;
  }
  .header .meta {
    font-size: 12px;
    color: #7d8590;
    margin-top: 2px;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .header-logo {
    height: 72px;
    border-radius: 6px;
  }
  .header-logo-right {
    height: 72px;
    border-radius: 6px;
  }
  .counts {
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
    align-items: center;
  }
  .count-badge {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
  }
  .count-badge .num {
    font-size: 20px;
    font-weight: 700;
    color: #e6edf3;
  }
  .count-badge .label {
    color: #7d8590;
    margin-left: 6px;
  }
  .counts-divider {
    width: 1px;
    height: 32px;
    background: #21262d;
  }
  .count-badge.usage .num { color: #d2a8ff; font-size: 16px; }
  .count-badge.usage .label { font-size: 11px; }
  .count-badge.system .num { font-size: 16px; }
  .count-badge.system .label { font-size: 11px; }
  .count-badge.system .num.ok { color: #3fb950; }
  .count-badge.system .num.warn { color: #d29922; }
  .count-badge.system .num.crit { color: #f85149; }
  .count-badge.railway .num { color: #58a6ff; font-size: 16px; }
  .count-badge.railway .label { font-size: 11px; }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 24px;
  }
  th {
    text-align: left;
    padding: 10px 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #7d8590;
    border-bottom: 1px solid #21262d;
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid #161b22;
    vertical-align: top;
  }
  tr.session:hover { background: #161b22; }
  tr.thread { background: #0d1117; }
  tr.thread td { padding-left: 36px; font-size: 13px; color: #8b949e; }
  tr.thread:hover { background: #13181f; }
  .name-cell {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot-ok { background: #3fb950; box-shadow: 0 0 6px #3fb95066; }
  .dot-warning { background: #d29922; box-shadow: 0 0 6px #d2992266; }
  .dot-idle { background: #484f58; box-shadow: 0 0 4px #484f5844; }
  .status {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }
  .status-running { background: #23853320; color: #3fb950; border: 1px solid #23853350; }
  .status-thinking { background: #8b5cf620; color: #a78bfa; border: 1px solid #8b5cf650; }
  .status-idle { background: #21262d; color: #7d8590; border: 1px solid #30363d; }
  .status-awaiting-approval { background: #d2992220; color: #d29922; border: 1px solid #d2992250; }
  .status-up { background: #1f6feb20; color: #58a6ff; border: 1px solid #1f6feb50; }
  .status-blocked { background: #9e6a0320; color: #d29922; border: 1px solid #9e6a0350; }
  .status-failed { background: #da363420; color: #f85149; border: 1px solid #da363450; }
  .status-done { background: #21262d; color: #7d8590; border: 1px solid #30363d; }
  .risk-text { color: #d29922; font-size: 12px; }
  .risk-none { color: #484f58; }
  .last-seen { color: #7d8590; font-size: 12px; }
  .tab-text {
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: #7d8590;
  }
  .task-text {
    max-width: 400px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .session-name {
    font-weight: 600;
    color: #e6edf3;
  }
  .thread-name {
    color: #8b949e;
    font-style: italic;
  }
  .empty-state {
    text-align: center;
    padding: 60px 20px;
    color: #484f58;
  }
  .empty-state h2 {
    font-size: 16px;
    margin-bottom: 8px;
    color: #7d8590;
  }
  .empty-state p { font-size: 13px; }
  .expired-section {
    border-top: 1px solid #21262d;
    padding-top: 16px;
    margin-top: 8px;
  }
  .expired-section h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #484f58;
    margin-bottom: 8px;
  }
  .expired-item {
    font-size: 12px;
    color: #484f58;
    padding: 2px 0;
  }
  .tooltip {
    position: relative;
    cursor: default;
  }
  .tooltip .tooltip-text {
    visibility: hidden;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 12px;
    position: absolute;
    z-index: 10;
    top: 100%;
    left: 0;
    margin-top: 4px;
    font-size: 12px;
    white-space: pre-line;
    min-width: 250px;
    max-width: 400px;
    color: #8b949e;
    box-shadow: 0 4px 12px #00000040;
  }
  .tooltip:hover .tooltip-text { visibility: visible; }
  .warn-banner {
    background: #9e6a0315;
    border: 1px solid #9e6a0340;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 20px;
    color: #d29922;
    font-size: 13px;
    display: none;
  }
  .warn-banner.visible { display: block; }
  /* Service status indicators */
  .services-row {
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
  }
  .service-card {
    display: flex;
    align-items: center;
    gap: 12px;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 10px 16px;
    min-width: 200px;
  }
  .service-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .service-dot-online { background: #3fb950; box-shadow: 0 0 8px #3fb95066; }
  .service-dot-offline { background: #f85149; box-shadow: 0 0 8px #f8514966; animation: pulse-dot 1.5s ease-in-out infinite; }
  .service-dot-checking { background: #d29922; box-shadow: 0 0 6px #d2992266; animation: pulse-dot 1s ease-in-out infinite; }
  @keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .service-info { display: flex; flex-direction: column; }
  .service-name { font-size: 13px; font-weight: 600; color: #e6edf3; }
  .service-url { font-size: 11px; color: #484f58; }
  .service-status { margin-left: auto; text-align: right; }
  .service-label { font-size: 11px; font-weight: 600; }
  .service-label-online { color: #3fb950; }
  .service-label-offline { color: #f85149; }
  .service-label-checking { color: #d29922; }
  .service-latency { font-size: 10px; color: #484f58; }

  /* Local pages grid */
  .pages-section {
    margin-bottom: 24px;
  }
  .pages-section h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #7d8590;
    margin-bottom: 10px;
    font-weight: 600;
  }
  .pages-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .page-link {
    display: flex;
    align-items: center;
    gap: 8px;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 8px 14px;
    text-decoration: none;
    color: #8b949e;
    font-size: 13px;
    transition: all 0.15s;
  }
  .page-link:hover {
    background: #1c2128;
    border-color: #30363d;
    color: #e6edf3;
  }
  .page-link svg {
    flex-shrink: 0;
    opacity: 0.5;
  }
  .page-link:hover svg { opacity: 0.9; }
  .page-link .route {
    font-size: 10px;
    color: #484f58;
    margin-left: 4px;
  }

  .watermark {
    position: fixed;
    bottom: 24px;
    pointer-events: none;
    z-index: 999;
  }
  .watermark-right { right: 24px; }
  .watermark-left { left: 24px; }
  .watermark img {
    width: 600px;
    height: auto;
    border-radius: 16px;
  }
</style>
</head>
<body>
  <div class="header">
    <div class="header-left">
      <img class="header-logo" src="{{LOGO_DATA_URI}}" alt="Logo" style="display:{{LOGO_DISPLAY}}">
    </div>
    <div class="header-center">
      <h1>WORKSTATE DASHBOARD</h1>
      <div class="meta">auto-refresh: 5s</div>
    </div>
    <img class="header-logo-right" src="{{GUS_LOGO_URI}}" alt="GUS.ai" style="display:{{GUS_LOGO_DISPLAY}}">
  </div>
  <div id="warn-banner" class="warn-banner"></div>
  <div class="counts" id="counts"></div>

  <!-- Service status -->
  <div class="services-row" id="services">
    <div class="service-card" id="svc-frontend">
      <div class="service-dot service-dot-checking" id="svc-fe-dot"></div>
      <div class="service-info">
        <span class="service-name">Frontend</span>
        <span class="service-url">localhost:3000</span>
      </div>
      <div class="service-status">
        <div class="service-label service-label-checking" id="svc-fe-label">Checking...</div>
        <div class="service-latency" id="svc-fe-latency"></div>
      </div>
    </div>
    <div class="service-card" id="svc-backend">
      <div class="service-dot service-dot-checking" id="svc-be-dot"></div>
      <div class="service-info">
        <span class="service-name">Backend API</span>
        <span class="service-url">localhost:8001</span>
      </div>
      <div class="service-status">
        <div class="service-label service-label-checking" id="svc-be-label">Checking...</div>
        <div class="service-latency" id="svc-be-latency"></div>
      </div>
    </div>
    <div style="width:1px;background:#21262d;margin:4px 0"></div>
    <div class="service-card" id="svc-web-fe">
      <div class="service-dot service-dot-checking" id="svc-wfe-dot"></div>
      <div class="service-info">
        <span class="service-name">Web Frontend</span>
        <span class="service-url">app.apt-gus.ai</span>
      </div>
      <div class="service-status">
        <div class="service-label service-label-checking" id="svc-wfe-label">Checking...</div>
        <div class="service-latency" id="svc-wfe-latency"></div>
      </div>
    </div>
    <div class="service-card" id="svc-web-be">
      <div class="service-dot service-dot-checking" id="svc-wbe-dot"></div>
      <div class="service-info">
        <span class="service-name">Web Backend</span>
        <span class="service-url">api.apt-gus.ai</span>
      </div>
      <div class="service-status">
        <div class="service-label service-label-checking" id="svc-wbe-label">Checking...</div>
        <div class="service-latency" id="svc-wbe-latency"></div>
      </div>
    </div>
  </div>

  <!-- Local pages -->
  <div class="pages-section">
    <h3>Local Pages</h3>
    <div class="pages-grid">
      <a class="page-link" href="http://localhost:3000/" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
        Chat <span class="route">/</span>
      </a>
      <a class="page-link" href="http://localhost:3000/digital-twin" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        Digital Twin <span class="route">/digital-twin</span>
      </a>
      <a class="page-link" href="http://localhost:3000/code-graph" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
        Code Graph <span class="route">/code-graph</span>
      </a>
      <a class="page-link" href="http://localhost:3000/knowledge-graph" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
        Knowledge Graph <span class="route">/knowledge-graph</span>
      </a>
      <a class="page-link" href="http://localhost:3000/docs-graph" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Docs Graph <span class="route">/docs-graph</span>
      </a>
      <a class="page-link" href="http://localhost:3000/chart" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        Trend Chart <span class="route">/chart</span>
      </a>
      <a class="page-link" href="http://localhost:3000/feedback" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        Report Issue <span class="route">/feedback</span>
      </a>
      <a class="page-link" href="#" onclick="fetch('/api/launch-pwsh').then(()=>this.style.color='#3fb950');return false;">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
        PowerShell 7 <span class="route">pwsh</span>
      </a>
    </div>
  </div>

  <div class="pages-section">
    <h3>Web Pages</h3>
    <div class="pages-grid">
      <a class="page-link" href="https://app.apt-gus.ai/" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
        Chat <span class="route">app.apt-gus.ai</span>
      </a>
      <a class="page-link" href="https://app.apt-gus.ai/digital-twin" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        Digital Twin <span class="route">/digital-twin</span>
      </a>
      <a class="page-link" href="https://app.apt-gus.ai/code-graph" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
        Code Graph <span class="route">/code-graph</span>
      </a>
      <a class="page-link" href="https://app.apt-gus.ai/knowledge-graph" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
        Knowledge Graph <span class="route">/knowledge-graph</span>
      </a>
      <a class="page-link" href="https://app.apt-gus.ai/docs-graph" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Docs Graph <span class="route">/docs-graph</span>
      </a>
      <a class="page-link" href="https://app.apt-gus.ai/chart" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        Trend Chart <span class="route">/chart</span>
      </a>
      <a class="page-link" href="https://app.apt-gus.ai/feedback" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        Report Issue <span class="route">/feedback</span>
      </a>
      <a class="page-link" href="https://gus-docs.pages.dev/" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
        Docs <span class="route">gus-docs.pages.dev</span>
      </a>
      <a class="page-link" href="https://apt-gus.ai/" target="_blank">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
        Landing Page <span class="route">apt-gus.ai</span>
      </a>
    </div>
  </div>

  <div id="content"></div>
  <div id="expired-section"></div>
  <div class="watermark watermark-left" style="display:{{LOGO_LEFT_DISPLAY}}">
    <img src="{{LOGO_LEFT_URI}}" alt="">
  </div>
  <div class="watermark watermark-right" style="display:{{LOGO_RIGHT_DISPLAY}}">
    <img src="{{LOGO_RIGHT_URI}}" alt="">
  </div>

<script>
const REFRESH_MS = 5000;

function relativeSafe(s) { return s || '?'; }

function statusClass(status) {
  const s = (status || '').toLowerCase().replace(/\\s+/g, '-');
  if (s === 'running') return 'status-running';
  if (s === 'thinking') return 'status-thinking';
  if (s === 'idle') return 'status-idle';
  if (s === 'awaiting-approval') return 'status-awaiting-approval';
  if (s === 'up') return 'status-up';
  if (s === 'blocked') return 'status-blocked';
  if (s === 'failed') return 'status-failed';
  return 'status-done';
}

function dotClass(staleness) {
  if (staleness === 'ok') return 'dot-ok';
  if (staleness === 'warning') return 'dot-warning';
  if (staleness === 'idle') return 'dot-idle';
  return 'dot-idle';
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text || '';
  return d.innerHTML;
}

function truncate(text, max) {
  if (!text) return '';
  return text.length > max ? text.slice(0, max) + '...' : text;
}

function fmtTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}
function pctClass(v) { return v >= 90 ? 'crit' : v >= 70 ? 'warn' : 'ok'; }

function renderTable(data) {
  const { sessions, expired, counts, usage, system, railway } = data;

  // Counts + Usage + System + Railway
  const countsEl = document.getElementById('counts');
  const totalThreads = sessions.reduce((sum, s) => sum + (s.threads || []).length, 0);
  const u = usage || {};
  const totalTokens = (u.input_tokens||0) + (u.output_tokens||0) + (u.cache_write_tokens||0) + (u.cache_read_tokens||0);
  const sys = system || {};
  const rw = railway || {};
  const rwBe = rw.backend || {};
  const rwFe = rw.frontend || {};
  const rwEst = rw.estimated || {};
  countsEl.innerHTML = `
    <div class="count-badge"><span class="num">${counts.sessions}</span><span class="label">sessions</span></div>
    <div class="count-badge"><span class="num">${totalThreads}</span><span class="label">threads</span></div>
    <div class="counts-divider"></div>
    <div class="count-badge usage"><span class="num">${fmtTokens(totalTokens)}</span><span class="label">tokens</span></div>
    <div class="count-badge usage"><span class="num">$${(u.cost_usd||0).toFixed(2)}</span><span class="label">cost</span></div>
    <div class="counts-divider"></div>
    <div class="count-badge system"><span class="num ${pctClass(sys.cpu_pct||0)}">${sys.cpu_pct||0}%</span><span class="label">CPU</span></div>
    <div class="count-badge system"><span class="num ${pctClass(sys.mem_pct||0)}">${sys.mem_pct||0}%</span><span class="label">RAM ${sys.mem_used_gb||0}/${sys.mem_total_gb||0} GB</span></div>
    <div class="count-badge system"><span class="num ${pctClass(sys.disk_pct||0)}">${sys.disk_pct||0}%</span><span class="label">Disk ${sys.disk_used_gb||0}/${sys.disk_total_gb||0} GB</span></div>
    <div class="counts-divider"></div>
    <div class="count-badge railway"><span class="num">${rwBe.cpu||0}%</span><span class="label">RW BE CPU</span></div>
    <div class="count-badge railway"><span class="num">${rwBe.mem_mb||0}</span><span class="label">MB BE RAM</span></div>
    <div class="count-badge railway"><span class="num">${rwFe.cpu||0}%</span><span class="label">RW FE CPU</span></div>
    <div class="count-badge railway"><span class="num">${rwFe.mem_mb||0}</span><span class="label">MB FE RAM</span></div>
    <div class="count-badge railway"><span class="num">${rwEst.net_tx_gb||0}</span><span class="label">GB egress</span></div>
  `;

  // Warning banner
  const banner = document.getElementById('warn-banner');
  const activeCount = counts.sessions;
  if (activeCount >= 3) {
    banner.textContent = 'Advisory: ' + activeCount + ' active sessions. Consider whether all threads need your attention right now.';
    banner.classList.add('visible');
  } else {
    banner.classList.remove('visible');
  }

  // Content
  const content = document.getElementById('content');
  if (sessions.length === 0) {
    content.innerHTML = `
      <div class="empty-state">
        <h2>No active sessions</h2>
        <p>Claude Code sessions will appear here when they report in.</p>
      </div>`;
  } else {
    let html = `<table>
      <thead><tr>
        <th style="width:30px">#</th>
        <th>Session</th>
        <th>Tab</th>
        <th>Task</th>
        <th>Status</th>
        <th>Risk</th>
        <th>Last seen</th>
      </tr></thead><tbody>`;

    sessions.forEach((s, i) => {
      const historyTip = s.history && s.history.length > 0
        ? s.history.map(h => escapeHtml(h)).join('\\n')
        : 'No history';
      html += `<tr class="session">
        <td>${i + 1}</td>
        <td>
          <div class="name-cell tooltip">
            <span class="dot ${dotClass(s.staleness)}"></span>
            <span class="session-name">${escapeHtml(s.name)}</span>
            <span class="tooltip-text">${historyTip}</span>
          </div>
        </td>
        <td class="tab-text">${escapeHtml(s.tab || '')}</td>
        <td><div class="task-text">${escapeHtml(s.task)}</div></td>
        <td><span class="status ${statusClass(s.status)}">${escapeHtml(s.status)}</span></td>
        <td class="${s.risk === '-' ? 'risk-none' : 'risk-text'}">${escapeHtml(s.risk)}</td>
        <td class="last-seen">${relativeSafe(s.last_seen_relative)}</td>
      </tr>`;

      (s.threads || []).forEach((t, ti) => {
        html += `<tr class="thread">
          <td style="color:#484f58">${i + 1}.${ti + 1}</td>
          <td>
            <div class="name-cell">
              <span class="dot ${dotClass(t.staleness)}"></span>
              <span class="thread-name">${escapeHtml(t.name)}</span>
            </div>
          </td>
          <td></td>
          <td><div class="task-text">${escapeHtml(t.task)}</div></td>
          <td><span class="status ${statusClass(t.status)}">${escapeHtml(t.status)}</span></td>
          <td class="${t.risk === '-' ? 'risk-none' : 'risk-text'}">${escapeHtml(t.risk)}</td>
          <td class="last-seen">${relativeSafe(t.last_seen_relative)}</td>
        </tr>`;
      });
    });

    html += '</tbody></table>';
    content.innerHTML = html;
  }

  // Expired section
  const expiredEl = document.getElementById('expired-section');
  if (expired && expired.length > 0) {
    let ehtml = '<div class="expired-section"><h3>Recently Expired</h3>';
    expired.slice().reverse().forEach(e => {
      ehtml += `<div class="expired-item">"${escapeHtml(e.name)}" - last task: ${escapeHtml(e.last_task)}</div>`;
    });
    ehtml += '</div>';
    expiredEl.innerHTML = ehtml;
  } else {
    expiredEl.innerHTML = '';
  }
}

async function refresh() {
  try {
    const resp = await fetch('/api/sessions');
    const data = await resp.json();
    renderTable(data);
  } catch (err) {
    // Server might be restarting, ignore
  }
}

refresh();
setInterval(refresh, REFRESH_MS);

// Service health checks
function updateService(prefix, online, latencyMs) {
  const dot = document.getElementById('svc-' + prefix + '-dot');
  const label = document.getElementById('svc-' + prefix + '-label');
  const lat = document.getElementById('svc-' + prefix + '-latency');
  if (online === null) {
    dot.className = 'service-dot service-dot-checking';
    label.className = 'service-label service-label-checking';
    label.textContent = 'Checking...';
    lat.textContent = '';
  } else if (online) {
    dot.className = 'service-dot service-dot-online';
    label.className = 'service-label service-label-online';
    label.textContent = 'Online';
    lat.textContent = latencyMs !== null ? latencyMs + 'ms' : '';
  } else {
    dot.className = 'service-dot service-dot-offline';
    label.className = 'service-label service-label-offline';
    label.textContent = 'Offline';
    lat.textContent = '';
  }
}

async function checkServices() {
  // Backend
  try {
    const t0 = performance.now();
    const r = await fetch('http://localhost:8001/health', { signal: AbortSignal.timeout(5000) });
    updateService('be', r.ok, Math.round(performance.now() - t0));
  } catch { updateService('be', false, null); }
  // Frontend
  try {
    const t0 = performance.now();
    const r = await fetch('http://localhost:3000/', { mode: 'no-cors', signal: AbortSignal.timeout(5000) });
    updateService('fe', true, Math.round(performance.now() - t0));
  } catch { updateService('fe', false, null); }
}

checkServices();
setInterval(checkServices, 8000);

// Web (cloud) service checks
async function checkWebServices() {
  // Web backend — api.apt-gus.ai/health (no-cors: CORS blocks cross-origin from localhost)
  try {
    const t0 = performance.now();
    const r = await fetch('https://api.apt-gus.ai/health', { mode: 'no-cors', signal: AbortSignal.timeout(8000) });
    updateService('wbe', true, Math.round(performance.now() - t0));
  } catch { updateService('wbe', false, null); }
  // Web frontend — app.apt-gus.ai
  try {
    const t0 = performance.now();
    const r = await fetch('https://app.apt-gus.ai/', { mode: 'no-cors', signal: AbortSignal.timeout(8000) });
    updateService('wfe', true, Math.round(performance.now() - t0));
  } catch { updateService('wfe', false, null); }
}

checkWebServices();
setInterval(checkWebServices, 15000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_logo(path_str):
    """Load an image file and return a data URI, or empty string on failure."""
    try:
        p = Path(path_str)
        if not p.exists():
            print(f"Warning: logo file not found: {p}")
            return ""
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode()
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                "gif": "gif", "svg": "svg+xml", "webp": "webp"}.get(ext, "jpeg")
        return f"data:image/{mime};base64,{b64}"
    except Exception as e:
        print(f"Warning: could not load logo: {e}")
        return ""


def main():
    global LOGO_DATA_URI, GUS_LOGO_URI, LOGO_LEFT_URI, LOGO_RIGHT_URI

    parser = argparse.ArgumentParser(
        description="Workstate Dashboard - local multi-session status aggregator"
    )
    parser.add_argument("--port", type=int, default=7777, help="Port (default: 7777)")
    parser.add_argument("--logo", type=str, default="", help="Path to header logo image")
    parser.add_argument("--logo-left", type=str, default="", help="Path to bottom-left logo image")
    parser.add_argument("--logo-right", type=str, default="", help="Path to bottom-right logo image")
    args = parser.parse_args()

    # Auto-load images from tools/images/ folder if no flags given
    images_dir = Path(__file__).parent / "images"
    if not args.logo and images_dir.exists():
        for name in ("logo", "header"):
            for ext in ("png", "jpg", "jpeg", "svg", "webp"):
                candidate = images_dir / f"{name}.{ext}"
                if candidate.exists():
                    args.logo = str(candidate)
                    break
            if args.logo:
                break
    if not args.logo_left and images_dir.exists():
        for ext in ("png", "jpg", "jpeg", "svg", "webp"):
            candidate = images_dir / f"logo-left.{ext}"
            if candidate.exists():
                args.logo_left = str(candidate)
                break
    if not args.logo_right and images_dir.exists():
        for ext in ("png", "jpg", "jpeg", "svg", "webp"):
            candidate = images_dir / f"logo-right.{ext}"
            if candidate.exists():
                args.logo_right = str(candidate)
                break

    if args.logo:
        LOGO_DATA_URI = load_logo(args.logo)
        if LOGO_DATA_URI:
            print(f"Header logo loaded: {args.logo}")
    # Auto-load GUS.ai logo from images/gusai_logo.png
    if images_dir.exists():
        for ext in ("png", "jpg", "jpeg", "svg", "webp"):
            candidate = images_dir / f"gusai_logo.{ext}"
            if candidate.exists():
                GUS_LOGO_URI = load_logo(str(candidate))
                if GUS_LOGO_URI:
                    print(f"GUS.ai logo loaded: {candidate}")
                break
    if args.logo_left:
        LOGO_LEFT_URI = load_logo(args.logo_left)
        if LOGO_LEFT_URI:
            print(f"Left logo loaded: {args.logo_left}")
    if args.logo_right:
        LOGO_RIGHT_URI = load_logo(args.logo_right)
        if LOGO_RIGHT_URI:
            print(f"Right logo loaded: {args.logo_right}")

    if not any([LOGO_DATA_URI, LOGO_LEFT_URI, LOGO_RIGHT_URI]):
        print(f"Tip: Drop images into {images_dir}/ to add logos:")
        print(f"  logo.png       -> header logo")
        print(f"  logo-left.png  -> bottom-left watermark")
        print(f"  logo-right.png -> bottom-right watermark")

    # Start sweeper thread
    t = threading.Thread(target=sweeper, daemon=True)
    t.start()

    # Start Claude Code session scanner
    scanner = threading.Thread(target=session_scanner, daemon=True)
    scanner.start()
    print(f"Claude Code session scanner active (scanning {CLAUDE_PROJECTS_DIR})")

    # Do initial scan immediately
    scan_claude_sessions()

    server = ThreadingHTTPServer(("", args.port), DashboardHandler)
    print(f"Workstate Dashboard: http://localhost:{args.port}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
