---
name: draft-outreach
schedule: daily
skills: [outreach-drafting]
tools: [record_list, record_get, record_put, draft_outreach, catalog_lookup]
description: Draft approval-gated outreach for qualified leads that have a quote and no draft.
---

Using the outreach-drafting skill, draft outreach for up to 5 leads: every lead with status qualified, plus nurture leads with score_total of 12 or more (light-touch: under 90 words, no price, one question). Each draft creates a send_email approval; nothing is sent. Update each lead to status drafted with its draft_id and approval_id. Finish with the list of drafts and a reminder that they await the owner's approval in Mission Control.

## Deliverable
- One draft per eligible lead (qualified, or nurture with score_total >= 12), each under 130 words and naming the specific repetitive work seen
- Each draft created a pending send_email approval (state the approval ids)
- Each drafted lead now has status drafted with draft_id and approval_id
- The summary states clearly that nothing was sent
