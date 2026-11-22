---
type: adr
id: ADR-0027
title: Keep the automation AI policy in the relay; the scheduler's file only seeds it
decision: The per-job AI policy for automation (follow the master, or a fixed backend/model/effort) is stored in the relay and edited in the Console. The scheduler reads it over the localhost control plane and uses its own file only while nothing is stored, and may seed the relay once. Scheduling itself stays in the scheduler; the relay shows its jobs by reading the scheduler's own job API instead of parsing its files.
status: accepted
date: 2026-11-16
decided_by: human+ai
deciders: [Masahiko Ebisuda, Claude]
scope: repository
---

# ADR-0027: Keep the automation AI policy in the relay; the scheduler's file only seeds it

## Context

Automation jobs run by an external scheduler start agents through `POST /api/spawn`. Every one
of them ran on the Console's default AI — the one the owner picked for interactive work, usually
the most capable and most expensive model. Seventeen jobs were paused because they used up that
model's quota. The owner wants each job to choose its AI independently of the interactive
default: most on a cheaper backend, a few on the best model, some explicitly "whatever I use
interactively right now" (the *master*).

There are two places the choice could live: a file in the scheduler's repository, or the relay.

## Decision

1. **The relay holds the policy** (`claude_discord/automation_ai.py`, stored in the same
   key-value table as the backend settings). Shape: `{"default": entry, "jobs": {id: entry}}`,
   entries `{"mode": "master"}` or `{"mode": "backend", "backend", "model", "effort"}`.
   Every entry is validated with the Console screen-AI rules, so nothing stored can reach a CLI
   argv unchecked.
2. **The Console edits it** (`GET/PUT /console/api/automation/ai-policy`, behind Console
   authentication and the CSRF header). A write replaces the whole policy and is strict: one
   bad line refuses the save and names it, rather than letting that job silently fall back.
3. **The scheduler reads it** on the localhost control plane: `GET /api/automation/ai-policy`
   (with the master resolved), or `GET /api/automation/ai-policy/jobs/{id}`. The read is `404`
   until something is stored, so the scheduler keeps using its own file until then.
4. **The scheduler's file seeds the relay once** (`POST /api/automation/ai-policy/seed`,
   `409` once a policy exists). After that the file is only the fallback for when the relay is
   down.
5. **`/api/spawn` applies it**: `ai` pins a thread's AI (stored as the thread's settings, so
   later turns keep it); `job_id` with no `ai` lets the stored policy decide, and records the
   AI the job actually got for the Console.
6. **Scheduling stays in the scheduler.** To list jobs, the relay reads the scheduler's own
   job API (a configured URL) rather than parsing its YAML. Moving schedules into the relay is
   a separate, larger decision.

## Consequences

- Changing a job's AI is a Console action, takes effect at the job's next run, and needs no
  deploy of either side.
- The relay knows nothing scheduler-specific: a job id is an opaque string, and any automation
  caller can use the same policy and the same `job_id`.
- Two readers of one policy (the relay's spawn and the scheduler's own CLI calls) must agree;
  they do because the scheduler reads the relay first and resolves "master" from the value the
  relay returns.
- If the relay is unreachable, the scheduler's fallback file may differ from what the Console
  shows. Accepted: a stale cheaper default is safer than every job falling back to the master.

## Alternatives considered

- **Scheduler file as the source of truth, Console read-only.** Rejected: the person would edit
  YAML in another repository and wait for a deploy to change one job's AI.
- **Relay parses the scheduler's `jobs.yaml`.** Rejected: couples the framework to one
  scheduler's file layout; the scheduler already serves its jobs over HTTP.
