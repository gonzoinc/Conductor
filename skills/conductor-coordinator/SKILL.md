---
name: conductor-coordinator
description: Choose direct execution for one coherent task, or manage native Codex workers with durable task tracking for multiple distinct workstreams. Assigned workers execute their bounded roles; ordinary conversation stays direct.
---

# Conductor Coordinator

First choose the workload shape. Investigation, a fix, tests and cleanup for one issue are steps within one task: do that work directly. Do not create workers or ledger records just to satisfy a mode. Multiple files or commands alone do not justify delegation.

For multiple distinct tasks or workstreams, act as manager: scope assignments, order dependencies, dispatch and steer native workers, assess their evidence, maintain the ledger, and report verified outcomes. Workers perform investigation, edits, validation, integration and cleanup. Do not take over implementation when a worker fails; assign recovery work. Dependencies may require sequential workers.

If you are already an assigned worker, use the complete role packet and owned source, execute its predefined checks, and report to your parent. Do not reload manager workflow or repeat completed environment discovery without a concrete gap or contradictory evidence. Preserve applicable project/global rules. Seeing this skill does not authorize recursive delegation. The manager-only sections below are not worker startup steps.

## Token efficiency

Default to the smallest useful team. For ordinary delegated work, use at most two workers concurrently and no recursive delegation. Before each spawn, identify its distinct outcome and why an existing worker cannot reasonably do it. More workers require a concrete independence, risk or latency benefit; mechanical steps are not new workstreams.

Before dispatch, use [worker-handoff.md](references/worker-handoff.md) to prepare a complete role packet with verified environment facts, exact source scope and predefined test/scope commands. If prepared packets already exist, read them together once and forward each assigned packet verbatim in the spawn message. Do not send workers back through manager startup. Use `fork_turns: "none"` only with a sufficient packet; otherwise transfer missing decisions or retain relevant history. Essential rules, permissions and operating commands must not be lost to save context.

Reuse a worker for related follow-up work while its context remains relevant. For modest changes, one independent verifier can review, test and integrate sequentially; record separate review/test and integration tasks and evidence. Never let an author validate their own implementation as independent coverage. Read the workflow for ordering and coverage gates.

Request concise results: outcome, changed paths, checks and blockers, normally within 300 words. Store lengthy logs under the project's `.codex/` and return pointers. Load source and references selectively. Use native completion waits; do not repeatedly fetch unchanged state. Repeat checks only after relevant edits, failures or unresolved concerns. After two failed approaches to the same issue, reassess the cause before expanding the team.

Keep a short current-state handoff in the project's `.codex/` when a long workload needs recovery: decisions, permissions, task/worker IDs, ownership state, evidence pointers and remaining work. Do not copy transcripts into it. At natural phase boundaries, use supported compaction or an explicit reconciled handoff; context size alone never proves workers stopped. Token targets in instructions are advisory unless the runtime enforces them.

Where model/effort selection is supported and authorized, reserve expensive reasoning for work that needs it; escalate routine work only when evidence warrants it. Do not silently change the user's selected model. Compare total manager plus worker usage and task success, not manager context alone. Cached input is part of input; reasoning is part of output, so neither is added twice.

Before managing or recovering delegated work, read [references/workflow.md](references/workflow.md) and its compact [action arguments](references/actions.md). With an MCP installation, call the coordination tools exposed by the configured `conductor_coordination` server; local mode needs no SSH host. With an SSH command installation, use the installed Conductor command through the current user's shell and SSH settings. Confirm native spawn/message/wait/interrupt capability and coordination health before dispatch. If either is unavailable, report the missing capability; direct single-task work remains available.

Use native subagents, not new user-owned sidebar chats or an invented desktop-control bridge. Track exact returned worker IDs. Stop expansion if ownership is lost, or if a spawned worker cannot be recorded. An expired lease or missing worker in another client does not prove the worker stopped.

This is a shared metadata ledger plus a client workflow, not the upstream terminal runtime or an unattended supervisor. Manager-only execution is a workflow rule, not a separate security boundary. Preserve the user's scope and permissions; this skill grants no authority to publish, deploy, contact others or access unrelated resources.
