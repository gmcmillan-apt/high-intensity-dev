# Contributing

This framework grows through shared experience. If you've found a pattern that helps you sustain high-intensity development, we want to hear about it.

## Submitting a Pattern

Patterns go in the `patterns/` directory. Each pattern should follow this structure:

### Format

```markdown
# Pattern Name

## One-line description of what this pattern does.

---

## The Problem
What failure mode does this address? Why does it happen specifically in AI-assisted development?

## The Pattern
What do you actually do? Be specific and actionable.

## Rules
When to apply it. When to stop. What the thresholds are.

## Implementation
How to set it up. Include both automated (Claude Code, tooling) and manual options where possible.

## The Key Insight
One paragraph. Why does this work? What's the principle underneath the practice?
```

### Guidelines

- **Be specific.** "Take breaks" is advice. "15 minutes every 90 minutes, enforced by a timer" is a pattern.
- **Be honest.** If you learned this by burning out, say so. The personal context is what makes these credible.
- **Include both automated and manual options.** Not everyone uses the same tools.
- **Keep it short.** If your pattern needs more than 300 lines to explain, it might be two patterns.

## Submitting a Claude Code Integration

If your pattern has a Claude Code implementation (a `CLAUDE.md` snippet), add it to `claude-code/` with a clear filename and a "Copy This" section that people can paste directly.

## Pull Requests

1. Fork the repo
2. Create a branch (`patterns/your-pattern-name`)
3. Add your pattern file
4. Open a PR with a brief description of the failure mode it addresses

## Questions or Ideas

Open an issue. Even if you don't have a fully formed pattern, describing a failure mode you've experienced is valuable. Someone else may have the solution.
