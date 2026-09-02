---
name: agent-commission
description: Turn a commission brief into a complete, runtime-valid agent spec (the five worker fields, five layers, core files, skills, tasks, tools, approvals, limits, pricing card). Use whenever a commission has no spec yet or a spec fails validation. Do not use to write code; the generator renders the package from the spec.
---

# Agent Commission

## When to Use
- A commission `.md` brief arrives without a spec, or a spec fails schema validation.
- NOT for: editing an existing product by hand, or inventing tools the runtime lacks.

## Quick Reference
- Worker = responsibility + trigger + tools + limits + reporting. Every spec states all five.
- Five layers: Context (core files + skills), Data (tools), Intelligence (model), Automate (schedule), Build (package).
- Tools come ONLY from the runtime registry: web_search, web_fetch, record_put, record_get, record_list,
  note_write, read_file, current_time, catalog_lookup, quote_price, draft_outreach, propose_action.
- Approval actions an agent may declare: send_email, publish_agent, deploy_agent, launch_agent, schedule_call.
- A task is `tasks/<name>.md` with frontmatter (name, schedule, skills, tools, description) and a `## Deliverable` checklist.
- A skill is `skills/<category>/<name>/SKILL.md` with When to Use / Quick Reference / Procedure / Pitfalls / Verification.
- Pricing card: model (per_seat | per_task | per_outcome | hybrid | custom_build), price, unit, rationale.

## Procedure
1. Read the brief. Extract audience, the job to be done, the outputs the buyer expects, and what must never happen.
2. Write `responsibility` as one sentence with a verb and an outcome; write `audience` as who pays.
3. Choose the smallest tool set that can do the job; start read-only; add write tools only for records the agent owns.
4. Declare approval actions for anything that leaves the agent: email, publishing, deploying, spending.
5. Design 2-4 tasks. Each has a schedule (daily | weekly | manual), the skills it loads, the tools it may use,
   a body that says what to do, and a `## Deliverable` checklist the verifier can judge.
6. Design 1-3 skills, each with the five sections. Instructions must be things the model could not have guessed.
7. Set limits: steps per task ≤ 12, tool calls per task ≤ 20, model calls per run ≤ 80, minutes per run ≤ 60.
8. Write the pricing card using the 2026 benchmarks in the seller's skill.
9. Return the spec as JSON matching the schema; list every assumption under `assumptions`.

## Pitfalls
- A tool name not in the registry fails validation; do not guess names.
- A task without a deliverable checklist cannot be verified.
- "Send" without an approval action is a violation; the runtime refuses it.
- Do not put secrets, real names of third parties, or local paths in any file.

## Verification
- [ ] Spec validates against the schema (the generator refuses otherwise).
- [ ] Every task's tools are in the agent's allowlist; every allowed tool exists.
- [ ] At least one task has a deliverable checklist with 3+ items.
- [ ] Approval actions cover every external effect.
- [ ] Pricing card present with a rationale.
