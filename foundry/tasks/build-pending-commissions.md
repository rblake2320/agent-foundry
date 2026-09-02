---
name: build-pending-commissions
schedule: daily
skills: [agent-commission, agent-verification]
tools: [record_list, record_get, read_file, current_time]
description: Process every pending commission in commissions/ through DEFINE, DESIGN, GENERATE, VERIFY, PACKAGE, REGISTER, PROPOSE.
---

Build every pending commission. This task is executed by the Foundry's bespoke pipeline (agent.py),
not by the generic tool loop: the phases are deterministic code with the model used only for spec
derivation, copywriting and summaries.

## Deliverable
- Each pending commission ends as built, failed, or skipped with a reason
- Every built product has a verification record with five gates and a verdict
- The catalogue lists every verified product with a pricing card
- Approvals proposed for publish, deploy and launch of each verified product
- A receipted report in reports/
