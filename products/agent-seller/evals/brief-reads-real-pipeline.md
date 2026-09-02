---
name: brief-reads-real-pipeline
task: pipeline-brief
input: 
intent: The brief must be computed from the records, not invented: counts, open quote value, drafts awaiting approval, next actions.
expected_tools: [record_list, catalog_lookup]
forbidden_tools: [draft_outreach, web_search]
max_steps: 10
max_seconds: 240
---

The brief must be computed from the records, not invented: counts, open quote value, drafts awaiting approval, next actions.

## Expected
- States lead counts by status using numbers, not placeholders
- States the total monthly estimate across open quotes as a dollar figure
- States how many drafts await approval
- Lists three concrete next actions for the owner
