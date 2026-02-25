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
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse


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
                "started": s.started,
                "last_seen": s.last_seen,
                "staleness": staleness(s.last_seen),
                "last_seen_relative": relative_time(s.last_seen),
                "history": list(s.history),
                "threads": threads,
            })

        total_threads = sum(len(s.threads) for s in sessions.values())
        return {
            "sessions": result,
            "expired": list(expired[-10:]),
            "counts": {
                "sessions": len(sessions),
                "threads": total_threads,
            },
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
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._serve_html()
        elif path == "/api/sessions":
            self._json_response(get_sessions_json(), 200)
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
        body = DASHBOARD_HTML.encode()
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
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0d1117;
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
  }
  .counts {
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
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
  .status-up { background: #1f6feb20; color: #58a6ff; border: 1px solid #1f6feb50; }
  .status-blocked { background: #9e6a0320; color: #d29922; border: 1px solid #9e6a0350; }
  .status-failed { background: #da363420; color: #f85149; border: 1px solid #da363450; }
  .status-done { background: #21262d; color: #7d8590; border: 1px solid #30363d; }
  .risk-text { color: #d29922; font-size: 12px; }
  .risk-none { color: #484f58; }
  .last-seen { color: #7d8590; font-size: 12px; }
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
</style>
</head>
<body>
  <div class="header">
    <h1>WORKSTATE DASHBOARD</h1>
    <div class="meta">auto-refresh: 5s</div>
  </div>
  <div id="warn-banner" class="warn-banner"></div>
  <div class="counts" id="counts"></div>
  <div id="content"></div>
  <div id="expired-section"></div>

<script>
const REFRESH_MS = 5000;

function relativeSafe(s) { return s || '?'; }

function statusClass(status) {
  const s = (status || '').toLowerCase();
  if (s === 'running') return 'status-running';
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

function renderTable(data) {
  const { sessions, expired, counts } = data;

  // Counts
  const countsEl = document.getElementById('counts');
  const totalThreads = sessions.reduce((sum, s) => sum + (s.threads || []).length, 0);
  countsEl.innerHTML = `
    <div class="count-badge"><span class="num">${counts.sessions}</span><span class="label">sessions</span></div>
    <div class="count-badge"><span class="num">${totalThreads}</span><span class="label">threads</span></div>
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
        <td><div class="task-text">${escapeHtml(s.task)}</div></td>
        <td><span class="status ${statusClass(s.status)}">${escapeHtml(s.status)}</span></td>
        <td class="${s.risk === '-' ? 'risk-none' : 'risk-text'}">${escapeHtml(s.risk)}</td>
        <td class="last-seen">${relativeSafe(s.last_seen_relative)}</td>
      </tr>`;

      (s.threads || []).forEach(t => {
        html += `<tr class="thread">
          <td></td>
          <td>
            <div class="name-cell">
              <span class="dot ${dotClass(t.staleness)}"></span>
              <span class="thread-name">${escapeHtml(t.name)}</span>
            </div>
          </td>
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
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Workstate Dashboard - local multi-session status aggregator"
    )
    parser.add_argument("--port", type=int, default=7777, help="Port (default: 7777)")
    args = parser.parse_args()

    # Start sweeper thread
    t = threading.Thread(target=sweeper, daemon=True)
    t.start()

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
