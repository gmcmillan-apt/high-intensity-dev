# Workstate Tracking

## Externalize parallel thread state to prevent invisible cognitive overload.

---

## The Problem

When you run multiple things in parallel — background agents, builds, test suites, servers, deploys — the state of those threads lives in your head. You're tracking:

- What's running
- What's blocked
- What depends on what
- What might conflict
- How long things have been going

This tracking feels free. It is not. Every thread you carry in working memory reduces your capacity for the actual work. The cognitive cost is invisible because there's no error message for "you're holding too much state in your head." You just get slower, make worse decisions, and feel tired without knowing why.

**The test:** If you cannot summarize what all active threads are doing without checking, you have too many active threads.

---

## The Pattern

Maintain a written system state snapshot whenever more than two concurrent threads are active.

### Format

| # | Thread | Task | Status | Risk | Started |
|---|--------|------|--------|------|---------|
| 1 | Backend server | uvicorn --reload --port 8001 | Up | - | 14:20 |
| 2 | Frontend server | npm run dev | Up | - | 14:21 |
| 3 | Agent (explore) | Searching auth module | Running | - | 14:30 |
| 4 | Agent (bash) | Running test suite | Running | Slow - 3min+ | 14:28 |

### Statuses

- **Up** — persistent process (servers, watchers, tunnels)
- **Running** — active task in progress
- **Done** — completed (remove after acknowledging)
- **Blocked** — waiting on something
- **Failed** — errored out, needs attention

### Risk

Freeform text. `-` for no risk. Examples:

- `Slow - 3min+` — thread running longer than expected
- `Schema conflict` — detected dependency issue
- `Port collision` — resource conflict with another thread
- `Stale - no update 5min` — possibly hung

---

## Rules

### When to add a row
- Starting a background process (server, watcher, tunnel)
- Spinning up a parallel agent or background task
- Kicking off a build, deploy, or test suite

### When to update a row
- Thread completes -> mark Done, then remove
- Thread fails -> mark Failed + risk note
- Conflict detected between threads -> add risk note to both
- Thread running longer than expected -> flag as risk

### When to warn
- **3+ active threads** -> surface the full table + a one-line advisory ("You have 4 active threads. Here's the state.")
- **Any thread running 5+ minutes without update** -> flag as potentially stale
- **Risk detected on any thread** -> surface immediately, don't wait to be asked

### When to clear
- Session end -> wipe the table (it's a scratch pad, not a log)
- All threads resolved -> table returns to empty

---

## Implementation: Claude Code (Automatic)

If you use [Claude Code](https://claude.ai/code), you can make this fully automatic. Your AI assistant is already the one creating the parallel work — it can track it as a byproduct.

Add the snippet from [CLAUDE-snippet.md](../claude-code/CLAUDE-snippet.md) to your project's `CLAUDE.md`. This instructs Claude Code to:

1. Maintain a `workstate.md` file automatically
2. Update it whenever background tasks are started/completed
3. Proactively print the table when thread count hits 3+
4. Flag stale or risky threads without being asked

**Why this works:** The whole point is that manual tracking defeats the purpose by adding more cognitive load. When the AI assistant tracks state as part of its existing workflow, the cost to you is zero.

### Trigger phrases

Say any of these to see the current state:

- "workstate"
- "what's running"
- "threads"
- "status"

---

## Implementation: Manual

If you don't use Claude Code, you can still use this pattern:

### Option A: Plain text file

Keep a `workstate.md` open in a split pane. Update it manually when you start/stop threads.

### Option B: Taskwarrior

```bash
# Start tracking a thread
task add "Backend server" project:workstate +running

# Mark done
task 1 done

# View active threads
task project:workstate status:pending
```

### Option C: Sticky note

Seriously. A physical sticky note on your monitor with 3-4 lines works. The medium doesn't matter. Externalization does.

---

---

## Implementation: Networked Dashboard (Multi-Session)

If you run multiple terminals/sessions in parallel (e.g., several PowerShell tabs each running Claude Code), the file-based approach doesn't scale — each session has its own context.

The **Workstate Dashboard** is a tiny local web server that all sessions POST their status to. One browser tab shows everything:

- All active sessions (your terminal tabs)
- All subagents within each session (background tasks Claude Code spun up)
- Staleness indicators (green/yellow/red dots)
- Auto-refresh every 5 seconds

```bash
# Start the dashboard
python tools/workstate-dashboard.py
# Open http://localhost:7777
```

See [tools/workstate-dashboard.py](../tools/workstate-dashboard.py) for the server and [CLAUDE-dashboard-snippet.md](../claude-code/CLAUDE-dashboard-snippet.md) for the CLAUDE.md integration.

Zero dependencies. Single Python file. Stdlib only.

---

## The Key Insight

The goal is not to track work more precisely. The goal is to **stop carrying state in your head** so your working memory is available for the actual problem you're solving.

The best implementation is the one you don't have to think about.
