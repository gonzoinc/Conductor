# Worker handoff

Before spawning, prepare one complete packet per role. Read the relevant project
rules and command inventory once as manager; include the applicable rules in the
packet. References are for substantive source evidence, not a replacement for
essential operating instructions. A worker needs:

- Objective, exact contract, canonical working directory and owned files.
- Role, dependencies, authorization limits and any read-only inputs.
- Checked environment facts: available tools, interpreter, service prerequisites
  and any known command/path constraints. Distinguish verified facts from guesses.
- Exact maintained test command, expected exit/result, and scope check. Prefer
  existing suites to improvised test code. Include required interpreter flags;
  for Python, use `-B` or equivalent bytecode prevention for disposable checks.
- Report format: outcome, changed paths, command/exit/result and real blockers.

Forward an existing prepared packet verbatim when it already meets this contract.
Use fresh context only when the packet is sufficient. Otherwise include the
missing decisions or retain relevant history; don't trade context for repeated
discovery. Keep model/effort and team choices within the user's instructions.

Tell assigned workers to start from their packet and owned source. They do not
reload manager workflow, probe a tool already verified unavailable, inspect
sibling tasks, or rerun manager health checks without new contradictory evidence
or a higher-priority requirement. They preserve applicable project/global rules.
Missing critical information is one targeted question to the manager.

Workers use the predefined command as given. On a failure, inspect the reported
failure and repair owned code if warranted; do not change immutable tests or
invent replacement expected values. Additional checks need a concrete uncovered
requirement. Scope checking uses the supplied inventory when available; don't
expand into unrelated Git roots or other experiment directories.

Prevent temporary artifacts where possible. Keep necessary working files in the
project's .codex. A rejected cleanup or permission operation is a blocker to
report, not permission to retry it using a different command, tool or identity.
Return concise evidence and stop when the assigned completion criteria pass.
