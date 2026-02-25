# Workstate Tracking — CLAUDE.md Snippet

Copy the section below into your project's `CLAUDE.md` file to enable automatic workstate tracking.

---

## Copy This Into Your CLAUDE.md

```markdown
## Workstate Tracking

Maintain a workstate table to externalize parallel thread state. This prevents invisible cognitive
overload when multiple background tasks are active.

### State File

Maintain the current state in `memory/workstate.md` (or print inline if no memory directory exists).

### Format

| # | Thread | Task | Status | Risk | Started |
|---|--------|------|--------|------|---------|
| 1 | Example | Description of what's running | Running | - | HH:MM |

Statuses: `Up` (persistent process), `Running` (active task), `Done`, `Blocked`, `Failed`
Risk: `-` for none, freeform text for flags

### When to Add a Row

- Spinning up a background agent (Task tool with run_in_background)
- Starting a background shell (Bash with run_in_background)
- Starting a persistent process (servers, watchers, tunnels)

### When to Update

- Agent/process completes -> status = Done, then remove after acknowledging
- Agent/process fails -> status = Failed + risk note
- Conflict detected between threads -> add risk to both rows

### When to Proactively Warn

- Active thread count hits 3+ -> print the full workstate table + one-line advisory
- Any thread running 5+ minutes without update -> flag as potentially stale
- Risk detected on any thread -> surface immediately

### Trigger Phrases

When the user says any of these, print the current workstate table:
- "workstate"
- "what's running"
- "threads"
- "status"

If no threads are active, respond: "No active threads."
```

---

## Notes

- The workstate table is session-scoped. It resets each conversation.
- This is advisory, not enforcement. It does not prevent starting new threads.
- The snippet works in any project — it has no dependencies on specific tools or frameworks.
- If your project uses a `memory/` directory for persistent notes, the state file goes there. Otherwise, just print the table inline when asked.
