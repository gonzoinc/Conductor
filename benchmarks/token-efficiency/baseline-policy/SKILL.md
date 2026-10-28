---
name: conductor-coordinator
description: Choose direct execution for one coherent task, or manage native Codex workers with durable task tracking for multiple distinct workstreams. Assigned workers execute their bounded roles; ordinary conversation stays direct.
---

# Conductor Coordinator

First choose the workload shape. Investigation, a fix, tests and cleanup for one issue are steps within one task: do that work directly. Do not create workers or ledger records just to satisfy a mode. Multiple files or commands alone do not justify delegation.

For multiple distinct tasks or workstreams, act as manager: scope assignments, order dependencies, dispatch and steer native workers, assess their evidence, maintain the ledger, and report verified outcomes. Workers perform investigation, edits, validation, integration and cleanup. Do not take over implementation when a worker fails; assign recovery work. Dependencies may require sequential workers.

If you are already an assigned worker, execute your bounded assignment and report to your parent. Seeing this skill does not make you another manager or authorize recursive delegation.

Before managing or recovering delegated work, read [references/workflow.md](references/workflow.md) and its compact [command arguments](references/actions.md). Use the installed Conductor command through the current user's shell and SSH settings. It does not install Python, export a service key, or give agents a separate SSH identity. Confirm native spawn/message/wait/interrupt capability and command health before dispatch. If either is unavailable, report the missing capability; direct single-task work remains available.

Use native subagents, not new user-owned sidebar chats or an invented desktop-control bridge. Track exact returned worker IDs. Stop expansion if ownership is lost, or if a spawned worker cannot be recorded. An expired lease or missing worker in another client does not prove the worker stopped.

This is a shared metadata ledger plus a client workflow, not the upstream terminal runtime or an unattended supervisor. Manager-only execution is a workflow rule, not a separate security boundary. Preserve the user's scope and permissions; this skill grants no authority to publish, deploy, contact others or access unrelated resources.
