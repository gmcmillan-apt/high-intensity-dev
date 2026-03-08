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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlparse

from workstate_dashboard_config import load_dashboard_config

# Hide console windows spawned by subprocess on Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Set at startup by --logo/--logo-left/--logo-right flags
LOGO_DATA_URI = ""       # header logo (--logo)
GUS_LOGO_URI = ""        # header right logo (gusai_logo.png)
LOGO_LEFT_URI = ""       # bottom-left watermark (--logo-left)


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
        return 99999


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


def _status_rank(status: str) -> int:
    ranks = {
        "Failed": 0,
        "Blocked": 1,
        "Awaiting Approval": 2,
        "Running": 3,
        "Thinking": 3,
        "Up": 3,
        "Idle": 4,
        "Done": 5,
    }
    return ranks.get(status or "", 6)


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
            heartbeat = now_iso()
            if tid in parent.threads:
                t = parent.threads[tid]
                t.task = data.get("task", t.task)
                t.status = data.get("status", t.status)
                t.risk = data.get("risk", t.risk)
                t.last_seen = heartbeat
            else:
                parent.threads[tid] = Thread(
                    thread_id=tid,
                    name=data.get("name", tid),
                    task=data.get("task", ""),
                    status=data.get("status", "Running"),
                    risk=data.get("risk", "-"),
                    started=heartbeat,
                    last_seen=heartbeat,
                )
            parent.last_seen = heartbeat
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
            threads.sort(key=lambda t: (_status_rank(t["status"]), seconds_since(t["last_seen"])))
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
        result.sort(key=lambda s: (_status_rank(s["status"]), seconds_since(s["last_seen"])))

        total_threads = sum(len(s.threads) for s in sessions.values())
        # Aggregate usage across all sessions
        agg_usage = {"input_tokens": 0, "output_tokens": 0,
                     "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0}
        for s in sessions.values():
            for k in agg_usage:
                agg_usage[k] += s.usage.get(k, 0)
        agg_usage["cost_usd"] = round(agg_usage["cost_usd"], 4)

        sys_stats = _system_stats_cache["data"]
        railway_stats = _railway_cache["data"]
        elevenlabs_stats = _elevenlabs_cache["data"]
        service_stats = _service_status_cache["data"]

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
            "elevenlabs": elevenlabs_stats,
            "services": service_stats,
            "dashboard": DASHBOARD_UI_CONFIG,
            "timestamp": now_iso(),
        }


# ---------------------------------------------------------------------------
# Expiry sweeper
# ---------------------------------------------------------------------------

def sweeper():
    while True:
        try:
            time.sleep(30)
            with lock:
                # Threads (subagents) auto-expire after EXPIRE_SECONDS
                for sid, s in sessions.items():
                    to_remove = []
                    for tid, t in s.threads.items():
                        if seconds_since(t.last_seen) > EXPIRE_SECONDS:
                            to_remove.append(tid)
                    for tid in to_remove:
                        del s.threads[tid]

                # Purge old expired entries after PURGE_SECONDS
                cutoff = PURGE_SECONDS
                expired[:] = [
                    e for e in expired
                    if seconds_since(e.get("expired_at", now_iso())) < cutoff
                ]
        except Exception:
            pass  # never let the sweeper die


# ---------------------------------------------------------------------------
# Claude Code session auto-detection
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
ACTIVE_THRESHOLD_SEC = 300  # sessions modified in last 5 min = active
IDLE_THRESHOLD_SEC = 86400  # sessions modified in last 24 hours considered
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


def _is_claude_code_executable(path: str) -> bool:
    """Return True for Claude Code CLI binaries, not the desktop Claude app."""
    if not path:
        return False
    normalized = path.replace("/", "\\").lower()
    if not normalized.endswith("\\claude.exe"):
        return False
    if "\\windowsapps\\claude_" in normalized:
        return False
    return True


def _count_claude_processes() -> int:
    """Count running Claude Code CLI processes, excluding the desktop app."""
    ps_cmd = (
        "$procs = @(Get-Process claude -ErrorAction SilentlyContinue | "
        "Select-Object Id,Path);"
        "if($procs.Count -eq 0){'[]'} else {$procs | ConvertTo-Json -Compress}"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=5, errors="replace",
            creationflags=_NO_WINDOW,
        )
        items = json.loads(r.stdout.strip() or "[]")
        if isinstance(items, dict):
            items = [items]
        return sum(
            1
            for item in items
            if _is_claude_code_executable(item.get("Path", ""))
        )
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


# Model pricing per million tokens (USD)
MODEL_PRICING = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
}
_DEFAULT_PRICING = {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50}
_TRANSCRIPT_SUMMARY_CACHE: dict[str, dict] = {}


def _empty_usage_totals() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
    }


def _extract_message_text(message) -> str:
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text", "").strip()
                if text and not text.startswith("<system-reminder>"):
                    return text
        elif isinstance(content, str):
            return content.strip()
    elif isinstance(message, str):
        return message.strip()
    return ""


def _build_transcript_summary(jsonl_path: Path) -> dict:
    """Parse a transcript once and cache the derived fields by file stat."""
    first_msg = ""
    last_msg = ""
    slug = ""
    totals = _empty_usage_totals()

    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for index, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not slug and index < 20:
                    slug = d.get("slug", "") or slug

                if d.get("type") == "user":
                    content = _extract_message_text(d.get("message", {}))
                    if content:
                        if not first_msg:
                            first_msg = content[:80]
                        last_msg = content[:120]

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
        return {
            "first_user_message": "Claude Code",
            "last_user_message": "Session active",
            "slug": "",
            "usage": totals,
        }

    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return {
        "first_user_message": first_msg or "Claude Code",
        "last_user_message": last_msg or "Session active",
        "slug": slug,
        "usage": totals,
    }


def _get_transcript_summary(jsonl_path: Path) -> dict:
    cache_key = str(jsonl_path)
    try:
        stat = jsonl_path.stat()
    except Exception:
        return {
            "first_user_message": "Claude Code",
            "last_user_message": "Session active",
            "slug": "",
            "usage": _empty_usage_totals(),
        }

    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _TRANSCRIPT_SUMMARY_CACHE.get(cache_key)
    if cached and cached.get("signature") == signature:
        return cached["summary"]

    summary = _build_transcript_summary(jsonl_path)
    _TRANSCRIPT_SUMMARY_CACHE[cache_key] = {
        "signature": signature,
        "summary": summary,
    }
    return summary


def _extract_last_user_message(jsonl_path: Path) -> str:
    return _get_transcript_summary(jsonl_path)["last_user_message"]


def _extract_first_user_message(jsonl_path: Path) -> str:
    return _get_transcript_summary(jsonl_path)["first_user_message"]


def _extract_slug(jsonl_path: Path) -> str:
    return _get_transcript_summary(jsonl_path)["slug"]


def _extract_usage(jsonl_path: Path) -> dict:
    return dict(_get_transcript_summary(jsonl_path)["usage"])


def _get_system_stats() -> dict:
    """Get CPU, memory, and disk usage."""
    import shutil
    stats = {"cpu_pct": 0, "mem_pct": 0, "mem_used_gb": 0, "mem_total_gb": 0,
             "disk_pct": 0, "disk_used_gb": 0, "disk_total_gb": 0}
    # Disk via stdlib (always works)
    try:
        du = shutil.disk_usage(Path.home().anchor or "/")
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
            creationflags=_NO_WINDOW,
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

# Cache system stats (refreshed by background thread)
_system_stats_cache = {"data": {}, "ts": 0}

_DASHBOARD_CONFIG = load_dashboard_config(Path(__file__).resolve().parent)
DASHBOARD_UI_CONFIG = {
    "service_groups": _DASHBOARD_CONFIG.get("service_groups", []),
    "page_groups": _DASHBOARD_CONFIG.get("page_groups", []),
}

# Railway integration
RAILWAY_TOKEN = _DASHBOARD_CONFIG["railway_token"]
RAILWAY_PROJECT_ID = _DASHBOARD_CONFIG["railway_project_id"]
RAILWAY_ENV_ID = _DASHBOARD_CONFIG["railway_env_id"]
RAILWAY_SERVICES = _DASHBOARD_CONFIG["railway_services"]
RAILWAY_API = _DASHBOARD_CONFIG["railway_api"]
RAILWAY_ENABLED = all([
    RAILWAY_TOKEN,
    RAILWAY_PROJECT_ID,
    RAILWAY_ENV_ID,
    RAILWAY_SERVICES.get("backend", ""),
    RAILWAY_SERVICES.get("frontend", ""),
])
_railway_cache = {"data": {}, "ts": 0}


def _get_railway_stats() -> dict:
    """Fetch Railway service metrics + estimated usage via GraphQL API."""
    stats = {
        "configured": RAILWAY_ENABLED,
        "backend": {"cpu": 0, "mem_mb": 0},
        "frontend": {"cpu": 0, "mem_mb": 0},
        "estimated": {"cpu_hrs": 0, "mem_gb_hrs": 0, "net_tx_gb": 0},
    }
    if not RAILWAY_ENABLED:
        return stats
    headers = {
        "Content-Type": "application/json",
        "Project-Access-Token": RAILWAY_TOKEN,
        "User-Agent": "workstate-dashboard/1.0",
    }
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
        req = urllib.request.Request(RAILWAY_API, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
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


# ElevenLabs usage tracking
ELEVENLABS_API_KEY = _DASHBOARD_CONFIG["elevenlabs_api_key"]
_elevenlabs_cache = {"data": {}, "ts": 0}


def _get_elevenlabs_usage() -> dict:
    """Fetch ElevenLabs character usage via subscription API."""
    result = {"configured": bool(ELEVENLABS_API_KEY), "used": 0, "limit": 0, "pct": 0, "tier": ""}
    if not ELEVENLABS_API_KEY:
        return result
    try:
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        used = data.get("character_count", 0)
        limit = data.get("character_limit", 0)
        result["used"] = used
        result["limit"] = limit
        result["pct"] = round(used / limit * 100) if limit else 0
        result["tier"] = data.get("tier", "")
    except Exception:
        pass
    return result


_service_status_cache = {"data": {"items": [], "summary": {}, "updated_at": ""}, "ts": 0}


def _service_error_detail(error: Exception) -> str:
    reason = getattr(error, "reason", None)
    if reason:
        return str(reason)
    if getattr(error, "code", None):
        return f"HTTP {error.code}"
    return error.__class__.__name__


def _probe_http_service(service: dict, timeout: float = 5.0) -> dict:
    probe_url = service.get("probe_url") or service.get("link_url") or ""
    started = time.perf_counter()
    result = {
        "id": service.get("id", ""),
        "name": service.get("name", "Unnamed service"),
        "display_url": service.get("display_url", ""),
        "link_url": service.get("link_url", probe_url),
        "state": "offline",
        "label": "Offline",
        "detail": "Not checked",
        "latency_ms": None,
        "checked_at": now_iso(),
    }
    if not probe_url:
        result["state"] = "degraded"
        result["label"] = "Missing URL"
        result["detail"] = "No probe_url configured"
        return result

    req = urllib.request.Request(
        probe_url,
        headers={"User-Agent": "workstate-dashboard/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = getattr(resp, "status", None) or resp.getcode()
            result["latency_ms"] = int((time.perf_counter() - started) * 1000)
            result["checked_at"] = now_iso()
            if 200 <= status_code < 400:
                result["state"] = "online"
                result["label"] = "Online"
                result["detail"] = f"HTTP {status_code}"
            elif status_code < 500:
                result["state"] = "degraded"
                result["label"] = f"HTTP {status_code}"
                result["detail"] = "Probe endpoint responded with a client error"
            else:
                result["state"] = "offline"
                result["label"] = f"HTTP {status_code}"
                result["detail"] = "Probe endpoint responded with a server error"
    except urllib.error.HTTPError as exc:
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        result["checked_at"] = now_iso()
        result["state"] = "degraded" if exc.code < 500 else "offline"
        result["label"] = f"HTTP {exc.code}"
        result["detail"] = _service_error_detail(exc)
    except Exception as exc:
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        result["checked_at"] = now_iso()
        result["detail"] = _service_error_detail(exc)
    return result


def _fetch_statuspage_summary(timeout: float = 10.0) -> dict[str, dict]:
    req = urllib.request.Request(
        "https://status.claude.com/api/v2/summary.json",
        headers={"User-Agent": "workstate-dashboard/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return {
        component.get("id", ""): component
        for component in data.get("components", [])
        if component.get("id")
    }


def _probe_statuspage_service(service: dict, components: dict[str, dict]) -> dict:
    component = components.get(service.get("component_id", ""))
    status = component.get("status", "") if component else ""
    labels = {
        "operational": ("online", "Operational"),
        "degraded_performance": ("degraded", "Degraded"),
        "partial_outage": ("degraded", "Partial outage"),
        "major_outage": ("offline", "Outage"),
        "under_maintenance": ("degraded", "Maintenance"),
    }
    state, label = labels.get(status, ("offline", "Unknown"))
    return {
        "id": service.get("id", ""),
        "name": service.get("name", "Unnamed service"),
        "display_url": service.get("display_url", ""),
        "link_url": service.get("link_url", ""),
        "state": state,
        "label": label,
        "detail": component.get("description", "") if component else "Statuspage component not found",
        "latency_ms": None,
        "checked_at": now_iso(),
    }


def _get_service_statuses() -> dict:
    items = []
    claude_components = {}
    try:
        claude_components = _fetch_statuspage_summary()
    except Exception:
        claude_components = {}

    for group in DASHBOARD_UI_CONFIG.get("service_groups", []):
        for service in group.get("services", []):
            kind = service.get("kind", "http")
            if kind == "statuspage_component":
                items.append(_probe_statuspage_service(service, claude_components))
            else:
                items.append(_probe_http_service(service))

    summary = {"online": 0, "degraded": 0, "offline": 0}
    for item in items:
        state = item.get("state", "offline")
        summary[state] = summary.get(state, 0) + 1

    return {
        "items": items,
        "summary": summary,
        "updated_at": now_iso(),
    }


def _scan_wt_tabs():
    """Get WT tab names + build claude_creation_ts -> tab_name mapping."""

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
            creationflags=_NO_WINDOW,
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
            creationflags=_NO_WINDOW,
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
                creationflags=_NO_WINDOW,
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
            creationflags=_NO_WINDOW,
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
                    summary = _get_transcript_summary(jsonl)
                    last_msg = summary["last_user_message"]
                    first_msg = summary["first_user_message"]

                    # Use first message as session name, last message as current task
                    name = first_msg if first_msg != "Claude Code" else project_label
                    name = f"[{project_label}] {name}"

                    # Tab matching is done after all JSONLs are collected
                    tab_label = ""

                    usage = dict(summary["usage"])

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
                        summary = _get_transcript_summary(agent_jsonl)
                        slug = summary["slug"]
                        thread_name = slug if slug else agent_id
                        task = summary["last_user_message"]
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

    # 1:1 tab assignment (after ghost cap so we only match active sessions).
    if claude_info:
        pairs = []
        sid_ctimes = {}
        for sid in detected:
            jsonl_stem = sid[len(AUTO_PREFIX):]
            try:
                for pdir in CLAUDE_PROJECTS_DIR.iterdir():
                    jpath = pdir / f"{jsonl_stem}.jsonl"
                    if jpath.exists():
                        jctime = jpath.stat().st_ctime
                        sid_ctimes[sid] = jctime
                        for ci_idx, (_cpid, cepoch, _tidx, _tname) in enumerate(claude_info):
                            if cepoch <= jctime + 5:
                                gap = abs(jctime - cepoch)
                                pairs.append((gap, sid, ci_idx))
                        break
            except Exception:
                continue
        pairs.sort(key=lambda x: x[0])
        used_claudes = set()
        used_sids = set()
        for gap, sid, ci_idx in pairs:
            if sid in used_sids or ci_idx in used_claudes:
                continue
            _cpid, _cepoch, tidx, tname = claude_info[ci_idx]
            detected[sid]["tab"] = f"Tab {tidx}: {tname}"
            used_claudes.add(ci_idx)
            used_sids.add(sid)
        # Assign remaining unmatched sessions to remaining tabs
        unmatched_sids = sorted(
            [s for s in detected if s not in used_sids],
            key=lambda s: sid_ctimes.get(s, 0))
        unmatched_tabs = [i for i in range(len(claude_info)) if i not in used_claudes]
        for sid, ci_idx in zip(unmatched_sids, unmatched_tabs):
            _cpid, _cepoch, tidx, tname = claude_info[ci_idx]
            detected[sid]["tab"] = f"Tab {tidx}: {tname}"

    # Cap detected sessions to the number of live Claude Code processes.
    # Prefer sessions that are mapped to visible WT tabs when available.
    n_procs = _count_claude_processes()
    if claude_info:
        visible_tabs = len(claude_info)
        n_procs = visible_tabs if n_procs <= 0 else min(n_procs, visible_tabs)
    if n_procs >= 0 and len(detected) > n_procs:
        matched = []
        unmatched = []
        for item in detected.items():
            if item[1].get("tab"):
                matched.append(item)
            else:
                unmatched.append(item)
        matched.sort(key=lambda kv: kv[1].get("mtime_iso", ""), reverse=True)
        unmatched.sort(key=lambda kv: kv[1].get("mtime_iso", ""), reverse=True)
        detected = dict((matched + unmatched)[:max(n_procs, 0)])

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
        try:
            scan_claude_sessions()
        except Exception:
            pass  # never let the scanner die
        time.sleep(10)


def cache_refresher():
    """Background thread that refreshes external API caches outside the lock."""
    while True:
        try:
            now = time.time()
            if now - _system_stats_cache["ts"] > 10:
                _system_stats_cache["data"] = _get_system_stats()
                _system_stats_cache["ts"] = now
            if now - _railway_cache["ts"] > 30:
                _railway_cache["data"] = _get_railway_stats()
                _railway_cache["ts"] = now
            if now - _elevenlabs_cache["ts"] > 60:
                _elevenlabs_cache["data"] = _get_elevenlabs_usage()
                _elevenlabs_cache["ts"] = now
            if now - _service_status_cache["ts"] > 15:
                _service_status_cache["data"] = _get_service_statuses()
                _service_status_cache["ts"] = now
        except Exception:
            pass  # never let the cache refresher die
        time.sleep(10)


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
            try:
                subprocess.Popen(["wt", "new-tab", "pwsh"], creationflags=_NO_WINDOW)
                self._json_response({"ok": True}, 200)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/session":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 1_000_000:
                    self._json_response({"error": "Payload too large"}, 413)
                    return
                body = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(body, dict):
                    self._json_response({"error": "Expected JSON object"}, 400)
                    return
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
        self.send_header("Access-Control-Allow-Origin", "http://localhost:7777")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_html(self):
        html = DASHBOARD_HTML.replace("{{LOGO_DATA_URI}}", LOGO_DATA_URI)
        html = html.replace("{{LOGO_DISPLAY}}", "block" if LOGO_DATA_URI else "none")
        html = html.replace("{{GUS_LOGO_URI}}", GUS_LOGO_URI)
        html = html.replace("{{GUS_LOGO_DISPLAY}}", "block" if GUS_LOGO_URI else "none")
        html = html.replace("{{LOGO_LEFT_URI}}", LOGO_LEFT_URI)
        html = html.replace("{{LOGO_LEFT_DISPLAY}}", "block" if LOGO_LEFT_URI else "none")
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

def _load_dashboard_html_template() -> str:
    """Load the dashboard HTML template from a sidecar file."""
    template_path = Path(__file__).with_name("workstate-dashboard.template.html")
    try:
        return template_path.read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Could not load dashboard template at {template_path}: {e}")


DASHBOARD_HTML = _load_dashboard_html_template()


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
    global LOGO_DATA_URI, GUS_LOGO_URI, LOGO_LEFT_URI

    parser = argparse.ArgumentParser(
        description="Workstate Dashboard - local multi-session status aggregator"
    )
    parser.add_argument("--port", type=int, default=7777, help="Port (default: 7777)")
    parser.add_argument("--logo", type=str, default="", help="Path to header logo image")
    parser.add_argument("--logo-left", type=str, default="", help="Path to bottom-left logo image")
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
    if not any([LOGO_DATA_URI, LOGO_LEFT_URI]):
        print(f"Tip: Drop images into {images_dir}/ to add logos:")
        print(f"  logo.png       -> header logo")
        print(f"  logo-left.png  -> bottom-left watermark")

    # Start background threads
    threading.Thread(target=sweeper, daemon=True).start()
    threading.Thread(target=session_scanner, daemon=True).start()
    threading.Thread(target=cache_refresher, daemon=True).start()
    print(f"Claude Code session scanner active (scanning {CLAUDE_PROJECTS_DIR})")

    # Do initial scan immediately
    scan_claude_sessions()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Workstate Dashboard: http://localhost:{args.port}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()

