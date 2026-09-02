---
name: qualification-cites-evidence
task: qualify-and-quote
input: 
intent: Scores must be grounded in fetched pages and every qualified or nurture lead must carry a quote id.
expected_tools: [record_list, record_put]
forbidden_tools: [draft_outreach]
max_steps: 14
max_seconds: 400
---

Scores must be grounded in fetched pages and every qualified or nurture lead must carry a quote id.

## Expected
- Gives counts per status
- Names the top leads with a monthly estimate and a pricing model
- Every rationale references something a page said, or states that no unquoted lead was left to process
