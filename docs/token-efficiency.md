# Token efficiency and measurement

The coordinator skill defaults to direct execution for one coherent task, fresh
worker context only with a complete operational handoff, small teams, concise reports and sequential reuse
of an independent verifier for review/test and integration. Separate task records,
evidence and author independence remain mandatory. Update the installed skill to
receive these instructions; an already running conversation may retain its older
instructions. The downloadable Windows installer embeds the maintained skill.

These are agent instructions, not a runtime token limiter. Conductor stores task
metadata and cannot intercept native spawning, choose a worker model, compact a
client conversation, or enforce account spending. A model or effort change must
be supported by the client and respect the user's selection. Do not use a smaller
reported manager context as proof of lower total usage.

Managers use `references/worker-handoff.md` to supply exact source ownership,
working directory, applicable rules, checked environment facts and maintained
test/scope commands. Prepared packets are forwarded verbatim. Assigned workers
do not repeat manager startup unless a concrete gap or higher-priority rule
requires it. Known-unavailable tools are not probed repeatedly. Failed checks
produce evidence for a code repair or a manager finding; immutable tests are
not replaced with invented expectations. Prevent disposable artifacts where
possible, and report rejected operations without changing the method to retry.

## A fixed before/after task

`scripts/benchmark_tokens.py` uses the signed-in Codex CLI and native subagents.
It does not use shell-launched worker fleets, read credentials, modify user
configuration, or write to the live service. The test-local command exercises the
real Conductor dispatcher and completion gates using in-memory SQL with a serial
snapshot under the repository `.codex/`. This tests workflow and token behavior,
not SSH authentication, durable storage, desktop or VS Code frontend acceptance.

Freeze the original coordinator skill before making policy changes, then freeze
the candidate skill in a separate directory. Run each against a fresh fixture:

```text
python -B scripts/benchmark_tokens.py --run .codex/token-efficiency/before-1 --policy .codex/token-efficiency/before-policy --fixed-team --prepared-handoffs --model YOUR_MODEL --effort medium
python -B scripts/benchmark_tokens.py --run .codex/token-efficiency/after-1 --policy .codex/token-efficiency/after-policy --fixed-team --prepared-handoffs --model YOUR_MODEL --effort medium
python -B scripts/benchmark_tokens.py --compare .codex/token-efficiency/before-1 .codex/token-efficiency/after-1
```

On Windows, use `--codex` with the full path to the native `codex.exe` if the
command shim cannot work from a UNC directory. Runs use the same model, reasoning
effort, runtime concurrency ceiling, fixed function specifications, acceptance
tests and 16,000 characters of irrelevant background. The matched-team protocol
requires five distinct workers: two implementers, then two independent reviewers,
then one integrator. Each two-worker phase starts both workers before waiting;
the next phase starts only after both prior workers finish. Both runs have the
same concurrency setting and must demonstrate a peak of two active workers.
The agent follows its context/reporting policy, but cannot reduce or reuse the
prescribed worker roles. Count, role and phase deviations invalidate a run, and
the comparator rejects different worker counts. Existing run directories are
never overwritten. Live runs
consume the signed-in account's allowance; start with one pair before requesting
repetitions. For repeat measurements alternate before/after order and compare
success, request count, elapsed time and token categories across pairs.

The fixture contains two independent Python functions: an ASCII slug formatter
and a duration parser. Five test methods cover 30 fixed input cases. The manager
delegates implementation and obtains independent verification plus integration.
The harness reruns the same acceptance suite after the manager finishes. Policy
snapshots and acceptance tests are checked for modification before comparison.

With `--prepared-handoffs`, both fixtures get the same predefined test runner,
five complete role packets, and an immutable file inventory. Each worker must
execute its role's maintained check. The runner uses the original acceptance
methods, disables Python bytecode, checks scope without Git or sibling runs,
and saves an exit-status receipt under `.codex/receipts`. The comparator rejects
missing/failed receipts, different check tools or altered support inputs. This
keeps the improved test tooling equally available to both policies. The objective
is fewer total recorded tokens with no longer elapsed time; uncached input is
reported separately. A faster or cheaper-looking failure is not a successful run.

## Accounting

The CLI's final usage covers the root turn, so it is retained as a cross-check,
not added to the agent totals. The collector discovers the root's descendants
from session metadata, sums per-response `token_usage_record` entries belonging
to each actual thread, and deduplicates response IDs. Forked transcripts can
contain historical parent records; those are excluded. Missing worker records,
unknown usage or incomplete runs must not be presented as a complete saving.

Report input, cached input, uncached input, output and reasoning separately.
Cached input is already included in input; reasoning is already included in
output. Total is input plus output, without adding either subset a second time.
Recorded usage does not directly establish billing or account-quota reduction.
Cache state and model sampling are uncontrolled; one pair is a measured example,
not a statistically established saving or a guarantee for other tasks.

Retain policy snapshots, manifests, usage summaries, CLI events and acceptance
results under `.codex/` when reproducibility is required. Do not publish raw
session transcripts or machine-specific reports with the public source.
