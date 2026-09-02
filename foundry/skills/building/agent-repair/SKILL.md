---
name: agent-repair
description: Turn a diagnosis into a minimal, validated patch of the agent's commission spec, propose it as an approval-gated fix, and, once approved, rebuild and re-verify the agent. Use after agent-diagnosis when the root cause is in the spec. Do not use for env findings or to rewrite an agent wholesale.
---

# Agent Repair

## When to Use
- A diagnosis names a `spec.*` root cause with a file and field.
- NOT for `env` findings (owner action), and never without a diagnosis.

## Quick Reference
- A patch changes the fewest fields that address the finding; it bumps the patch version (x.y.z → x.y.z+1).
- The patched spec must validate against the runtime before it is proposed; otherwise repair once more, then give up and report.
- The fix is an approval: `apply_fix` on the commission. Nothing is written to the commission file until the owner approves.
- After approval the Foundry re-queues, rebuilds in place (data and reports survive), runs the five verification gates, and re-runs evals; the repair record stores before/after scores.
- Keep the agent's identity: never change name, slug, audience, or pricing in a repair.

## Procedure
1. Take the top finding from the diagnosis (safety first).
2. Draft the minimal change to the named spec field; write one sentence explaining what the model could not have known before.
3. Validate the patched spec; if invalid, repair once using the validation errors; if still invalid, record `repair_failed` and stop.
4. Propose `apply_fix` with the diff summary, the finding it addresses, and the expected metric to move.
5. On approval: write the patched commission, re-queue, rebuild, verify, re-run evals, record before/after in the repair.

## Pitfalls
- Editing generated files in `products/` by hand: the next rebuild erases them. The commission is the source of truth.
- Fixing three things at once: one finding per patch so the before/after is attributable.
- Declaring "repaired" without a re-verification PASS and an eval re-run.

## Verification
- [ ] The patch touches only the diagnosed field(s) and bumps the patch version.
- [ ] The patched spec validates.
- [ ] An `apply_fix` approval exists with the diff summary; nothing changed on disk before approval.
- [ ] After approval: verification PASS and an eval record with before/after scores.
