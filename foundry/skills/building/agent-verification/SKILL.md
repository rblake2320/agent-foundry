---
name: agent-verification
description: Prove a built agent works before it is catalogued: doctor, hermetic tests, one real smoke task with a receipt, ledger integrity, agent card validity. Use after generation and before registering or proposing publish/deploy. Do not use to fix code; a failed verification sends the commission back to DEFINE with the failure recorded.
---

# Agent Verification

## When to Use
- Immediately after a package is generated, and again before any publish/deploy approval is proposed.
- NOT for: judging whether the agent is commercially good (that is the seller's job).

## Quick Reference
- Gate 1 — doctor: every required check passes inside the product folder.
- Gate 2 — tests: the package's own `tests/` pass (config, brain, tasks, tools allowlist, agent card).
- Gate 3 — smoke: one task runs on the real model within the product's own caps and produces a receipt.
- Gate 4 — ledger: the product's ledger verifies end to end after the smoke run.
- Gate 5 — card: `/.well-known/agent-card.json` has name, description, supportedInterfaces, version, capabilities, skills.
- Verdict = PASS only if all five gates pass. Anything else is FAIL with the first failing gate named.

## Procedure
1. Run the doctor with the product folder as root; record failed required checks.
2. Run `pytest -q` inside the product; record the summary line.
3. Run the first non-manual task (or the first task) with a tiny input; cap steps at the product's limits.
4. Verify the ledger; render the agent card and check required fields.
5. Write the verification record: gates, evidence (doctor lines, pytest line, run id, receipt), verdict.

## Pitfalls
- A smoke run that "completes" with an empty final output is not a pass; require non-empty output.
- Do not count a model outage as an agent failure; record `inconclusive` and retry once.
- Never edit the product during verification; verification is read-only plus one run.

## Verification
- [ ] Verification record saved with all five gates and evidence.
- [ ] Verdict is PASS or FAIL, never "mostly".
- [ ] The receipt run id exists in the product's own run table.
