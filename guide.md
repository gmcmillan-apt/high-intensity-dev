# The Guide

## Burnout Prevention for High-Intensity AI Development

*Written by Greg McMillan. Most of these lessons learned the hard way.*

If you are reading this because you are already fried: skip to the [Five Rules](#tldr--the-five-rules) below, then go outside.

---

## TL;DR — The Five Rules

1. **Define "done for today" before you start.** When the condition is met, stop. No extensions.
2. **Cap deep work at 4-5 hours.** Take 15 minutes off every 90 minutes. No exceptions during crunch.
3. **Shut down visibly.** Write tomorrow's first task, close everything, leave the workspace.
4. **Sleep is non-negotiable.** Two nights under 6.5 hours = mandatory reduced workload the next day.
5. **You are not the project.** If pausing for 3 months would destabilize your identity, recalibrate.

---

## 1. Core Principle

Sustained performance requires oscillation.

> High intensity -> Shutdown -> Recovery -> Return sharp

Continuous redline destroys clarity, judgment, and creativity. The goal is not maximum hours. The goal is maximum useful output per unit of cognitive investment, sustained over months and years.

---

## 2. The AI-Assisted Development Trap

This section exists because AI-paired development creates a unique burnout vector that traditional software engineering does not.

When you work with AI agents:

- **Results come in seconds.** There is no natural compile-wait or deploy-wait friction to create breathing room. You prompt, you get code, you prompt again. The feedback loop is so fast it bypasses your normal satiation signals.
- **"Just one more prompt" is the new "just one more commit."** Each result feels small and cheap. But 200 small prompts across 14 hours is a marathon your brain ran without ever deciding to.
- **Everything feels achievable.** AI makes your backlog feel infinite because your capability feels infinite. "I *could* build this in 2 hours" turns into "I *should* build this tonight." Capability does not equal obligation.
- **You lose track of time.** The rapid iteration cycle suppresses your awareness of elapsed time more than manual coding does. You look up and 6 hours have passed.

The countermeasure is deliberate friction: timers, defined stopping points, and a culture that treats "I stopped on time" as a sign of discipline, not a lack of ambition.

---

## 3. Why Overdrive Feels Good (The Neurochemistry)

When you are deep in a build session — solving a hard constraint, watching an architecture come together, shipping a feature — your brain is not just "focused." It is running a neurochemical cocktail:

- **Dopamine** — goal pursuit, problem solving, novelty seeking
- **Norepinephrine** — heightened focus, urgency, alertness
- **Cortisol** — stress mobilization, sustained energy
- **Endorphins** — reward signaling, pain dampening

That combination feels like buzzing under the skin, elevated heart rate, racing thoughts, and a sense of being "on edge but productive." It is essentially a mild fight-or-flight state that your brain has labeled as useful work.

This is why a 14-hour coding session can feel *great* while you are in it. Your nervous system is idling at 4,000 RPM. You feel sharp, fast, unstoppable. The problem is not the state itself — it is that it does not shut off when you close the laptop.

**The critical insight: flow and anxiety are physiologically almost identical.**

- Flow = high activation + sense of control + forward progress
- Anxiety = high activation + uncertainty + perceived threat

After weeks of sustained cognitive load, your body loses the ability to distinguish between them. What started as productive intensity becomes low-grade anxiety that follows you to bed, into the shower, and through your weekends.

**To break the cycle, your nervous system needs an explicit "threat resolved" signal:**

- **Parasympathetic breathing reset** — 4-second inhale through the nose, 6-8 second exhale through the mouth. Long exhales directly suppress norepinephrine. Two minutes is enough to shift your nervous system out of activation mode.
- **Physical discharge** — hard lifting, a fast walk, a rowing session, cold exposure. Cognitive strain without physical release builds pressure with no outlet. Movement tells your body the danger has passed.
- **Environment change** — leave the room where you work. Your brain associates the space with activation. Changing rooms is a surprisingly effective reset.

None of this is weakness. It is maintenance for the system that does the thinking.

---

## 4. Definition of Healthy Flow

**Healthy flow:**

- Has a clear start and stop
- Ends when the defined task is complete
- Does not significantly impair sleep
- Does not spill over compulsively into off-hours
- Leaves you tired but satisfied, not wired and restless

**Unhealthy activation:**

- Feels wired instead of focused
- Continues mentally after work stops (replaying architectures, debugging in your head)
- Interferes with sleep onset or quality
- Feels difficult to disengage from
- Produces irritability when interrupted

If you cannot tell which state you are in, you are probably in the second one.

---

## 5. Daily Work Structure

### 5.1 Define "Done for Today"

Before beginning a build session, write down:

> Today is successful if: _______

Examples:

- Feature X routes correctly
- Agent Y returns deterministic output
- Test Z passes on all cases
- Documentation for module W is complete

When this condition is met: **stop.**

No extensions due to momentum. Momentum is not a reason to continue. It is a signal that you are activated, which is exactly when discipline matters most.

### 5.2 Session Time Limits

- **Maximum deep work session: 4-5 hours.** After this, cognitive returns decline sharply even if you feel productive.
- **Break cadence: 15 minutes every 90 minutes.** Stand up. Walk. Look at something more than 20 feet away. Not your phone.
- **Maximum workday for sustained periods: 8-10 hours.** Occasional 12-hour pushes before a demo are human. Making them the norm is not.

Use a timer. Your internal clock is unreliable during flow state, doubly so during AI-paired sessions.

### 5.3 Hard Stop Protocol

When ending work:

1. Write down the next action for tomorrow (one sentence, specific enough to start immediately).
2. Save all files and push or stash work-in-progress.
3. Close terminals, IDEs, and browser tabs.
4. Leave the physical workspace.

No "just checking logs." No "quick look at that error." Shutdown must be visible and intentional.

---

## 6. Parallel Work and Cognitive Load

Running multiple systems, agents, or workstreams in parallel increases cognitive vigilance even when the systems are doing the work.

When more than two concurrent threads are active (parallel agents, background builds, multiple test suites):

**Maintain a written system state snapshot:**

| Thread | Task | Status | Risk |
|--------|------|--------|------|
| Agent A | Refactoring auth | Running | None |
| Agent B | DB migration | Blocked | Schema conflict |
| Build | Frontend deploy | Pending | - |

Never carry system state only in working memory. The cognitive cost of tracking parallel state in your head is invisible but enormous. Externalize it.

If you catch yourself unable to summarize what all active threads are doing without checking: you have too many active threads.

> See [Workstate Tracking](patterns/workstate-tracking.md) for a practical implementation of this pattern, including an automatic Claude Code integration.

---

## 7. Physical Health

Marathon sessions destroy more than your focus.

- **Eyes:** Follow the 20-20-20 rule. Every 20 minutes, look at something 20 feet away for 20 seconds. Screen brightness should match ambient light.
- **Wrists and hands:** If you feel any tingling, numbness, or ache, stop typing immediately. Stretch. RSI builds silently and can take months to heal.
- **Back and posture:** Stand up every hour. A 2-minute walk resets spinal compression. No amount of ergonomic equipment compensates for 6 hours of immobility.
- **Hydration:** Keep water at your desk. Dehydration mimics fatigue and impairs concentration before you notice it.
- **Food:** Eat real meals. Skipping lunch to keep a session going is a false economy. Your brain runs on glucose.

None of this is optional. It is infrastructure maintenance for the system that writes the code.

---

## 8. Sleep Protection Policy

Sleep is the primary guardrail against burnout and the single highest-leverage recovery tool.

**If sleep drops below 6.5 hours for two consecutive nights:**

- Reduce workload the following day
- No high-intensity architecture sessions
- No extended debugging marathons
- No parallel agent orchestration

**Non-negotiable sleep hygiene for build weeks:**

- Screens off 30 minutes before bed (yes, really)
- No "solving one last thing" from your phone in bed
- If your brain is still running architecture at midnight, write it down on paper and close the notebook

Chronic sleep disruption degrades judgment, emotional regulation, and the ability to assess your own impairment. It is the most dangerous burnout accelerant because it makes you worse at detecting that you are getting worse.

---

## 9. Weekly Recovery Cycle

Block a specific half-day each week — same day, same time — and protect it.

**During this time, no:**

- Architecture design
- Deep debugging
- Parallel system orchestration
- High-stakes code review

**Allowed activities:**

- Light planning and prioritization
- Documentation and cleanup
- Administrative tasks
- Exercise and movement
- Long-form thinking (reading, journaling, whiteboarding without a deadline)

Put it on your calendar. Treat it like a meeting that cannot be moved. If "I'll take a break when things slow down" is your strategy, you will never take a break because things do not slow down in this field.

Insight consolidates during recovery, not during constant execution.

---

## 10. The Infinite Backlog Problem

AI-assisted development creates a unique psychological pressure: because you *can* build almost anything in a few hours, the backlog feels like a moral weight. Every unbuilt feature feels like a personal failure of velocity.

This is a trap.

- A backlog is a menu, not a mandate.
- Saying "not now" to a feature is a decision, not a failure.
- The backlog will always be longer than your capacity. That is by design. It means you have options, not obligations.
- Shipping 3 solid features is worth more than shipping 10 fragile ones.

If the backlog is causing anxiety, the problem is not the backlog. The problem is the relationship to the backlog.

---

## 11. Identity Separation

Projects must not become the sole source of:

- Self-worth
- Intellectual validation
- Social identity
- Daily excitement
- Sense of momentum and purpose

Ask periodically:

> If this project paused for 3 months, would I still feel stable and grounded?

If the answer is no, workload and emotional attachment need recalibration. This is not a weakness. It is a structural dependency that makes you fragile.

You are an engineer who builds systems. You are not the system.

---

## 12. Warning Signs

**Individual warning signs:**

- Difficulty sleeping due to active problem-solving thoughts
- Compulsive feature ideation outside work hours
- Irritability when interrupted during work
- Persistent "wired" feeling that does not resolve with rest
- Mental exhaustion despite high productivity output
- Reduced enjoyment of activities unrelated to the project
- Skipping meals, exercise, or social commitments to keep coding
- Checking Slack, logs, or dashboards compulsively after shutdown

**Team warning signs:**

- Late-night commits becoming normalized
- "I pulled an all-nighter" treated as a badge of honor
- Team members competing on hours instead of outcomes
- Junior developers mimicking senior engineers' unsustainable habits

If multiple signs appear: initiate recovery protocol (next section).

---

## 13. Recovery Protocol

When activation exceeds sustainable levels:

1. **Enforce a full shutdown day.** Not a light day. A zero-development day.
2. **Engage in physical activity.** Walk, run, lift, swim. Something that is not sitting and thinking.
3. **Do not reopen development tools.** Not to check on a build. Not to read a log. Not to "quickly fix that one thing."
4. **Prioritize sleep.** Go to bed early. No alarms if possible.
5. **Reconnect with non-work identity.** See people. Cook a meal. Do something with your hands that is not a keyboard.

Return to development only after baseline calm is restored. You will know because the project will feel interesting again instead of urgent.

---

## 14. Leadership Responsibility

Senior engineers and project leads must:

- **Model shutdown behavior.** If you send messages at midnight, your team will think midnight is normal.
- **Never glorify marathon sessions.** "I coded for 16 hours" is not a flex. It is a process failure.
- **Encourage defined stopping points.** Ask your team "what's your done-for-today?" and mean it.
- **Prevent junior developers from internalizing redline culture.** They are watching everything you do. They will copy your worst habits faster than your best ones.
- **Normalize recovery.** Taking a break is not slacking. It is maintenance. Say this out loud and often.
- **Check in on workload, not just output.** "How are you holding up?" matters more than "is it done yet?"

---

## Final Principle

We do not chase unsustainable intensity.

We build systems — and habits — that allow us to enter deep focus deliberately and exit it deliberately.

Performance is not defined by hours or intensity.

It is defined by what you can sustain across years without breaking yourself or the people around you.

**Build sharp. Stop clean. Come back ready.**
