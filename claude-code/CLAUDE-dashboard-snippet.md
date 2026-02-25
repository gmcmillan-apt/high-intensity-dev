# Workstate Dashboard Reporting — CLAUDE.md Snippet

Copy the section below into your project's `CLAUDE.md` file to enable automatic dashboard reporting across multiple Claude Code sessions.

**Prerequisite:** Start the dashboard server first: `python tools/workstate-dashboard.py`

---

## Copy This Into Your CLAUDE.md

```markdown
## Workstate Dashboard Reporting

If the workstate dashboard is running (http://localhost:7777), report session status via curl.
All curl commands use `|| true` so a stopped dashboard never breaks your workflow.

### Session Registration (run once at conversation start)

At the start of every conversation, register this session:

\`\`\`bash
export WORKSTATE_ID=$(uuidgen)
export WORKSTATE_NAME="SESSION_NAME"
curl -s -X POST http://localhost:7777/api/session \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$WORKSTATE_ID\", \"name\": \"$WORKSTATE_NAME\", \"task\": \"Session started\", \"status\": \"Running\"}" > /dev/null 2>&1 || true
\`\`\`

Replace SESSION_NAME with whatever the user calls this session. If the user doesn't name it,
use a short description of the first task (e.g., "auth-refactor", "frontend-ui", "db-migration").

### Session Updates (call at task transitions)

Report whenever you start a materially different unit of work:

\`\`\`bash
curl -s -X POST http://localhost:7777/api/session \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$WORKSTATE_ID\", \"name\": \"$WORKSTATE_NAME\", \"task\": \"CURRENT_TASK\", \"status\": \"STATUS\", \"risk\": \"RISK_OR_DASH\"}" > /dev/null 2>&1 || true
\`\`\`

### Subagent/Thread Reporting

When launching background agents or background bash tasks, report them as child threads:

\`\`\`bash
curl -s -X POST http://localhost:7777/api/session \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$(uuidgen)\", \"parent_id\": \"$WORKSTATE_ID\", \"thread_id\": \"THREAD_ID\", \"name\": \"THREAD_NAME\", \"task\": \"DESCRIPTION\", \"status\": \"Running\"}" > /dev/null 2>&1 || true
\`\`\`

When a background agent/task completes, update it:

\`\`\`bash
curl -s -X POST http://localhost:7777/api/session \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$(uuidgen)\", \"parent_id\": \"$WORKSTATE_ID\", \"thread_id\": \"THREAD_ID\", \"name\": \"THREAD_NAME\", \"task\": \"DESCRIPTION\", \"status\": \"Done\"}" > /dev/null 2>&1 || true
\`\`\`

### When to Report

**Session-level (task transitions):**
- Starting a new logical unit of work ("Refactoring auth" -> "Writing tests")
- Status changes (Blocked, Failed, Done)
- Risk detection (slow operation, conflict, error)

**Thread-level (subagents):**
- Launching a background Task agent -> report as Running thread
- Background agent completes -> report as Done thread
- Background agent fails -> report as Failed thread with risk note

### Statuses

Up | Running | Done | Blocked | Failed

### Important

- Do NOT report every shell command — report logical work transitions
- Keep task descriptions concise (under 80 chars)
- The `|| true` ensures dashboard downtime never interrupts work
- Thread IDs should be descriptive: "agent-explore-auth", "bash-test-suite", "build-frontend"

### Session End

When the user says they're done or the conversation ends:

\`\`\`bash
curl -s -X POST http://localhost:7777/api/session \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$WORKSTATE_ID\", \"name\": \"$WORKSTATE_NAME\", \"task\": \"Session complete\", \"status\": \"Done\"}" > /dev/null 2>&1 || true
\`\`\`
```

---

## Notes

- **Session names matter.** The user glances at the dashboard and needs to instantly know which PS tab maps to which row. Use short, descriptive names.
- **Don't over-report.** One POST per logical task transition is enough. Not every `git status` or file read.
- **Threads auto-expire.** If a background agent crashes without reporting Done, the dashboard will mark it stale (yellow -> red) and eventually expire it.
- **Dashboard is optional.** The `|| true` on every curl means this is purely additive. Sessions work identically with or without the dashboard running.
