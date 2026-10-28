"""One bounded, low-cost check for a newly connected Codex client."""
from __future__ import annotations


QUICK_CHECK_PROMPT = (
    "Use $conductor-coordinator for a quick setup check. Keep it brief and create no files. "
    "First call coordination_health. Then answer 2+3 yourself, without a worker or ledger. "
    "Next create one disposable workload with two independent scout tasks: ask exactly two "
    "native workers to answer 7+5 and 9-4, one each. Record their returned IDs and one-line "
    "results, then complete the workload. Do not add reviewers, integrators, tests, or other "
    "workers. If health, native workers, or completion is unavailable, stop and report the "
    "blocker without expanding the test. Reply in at most four lines: health, direct answer, "
    "worker IDs/results, workload status."
)
