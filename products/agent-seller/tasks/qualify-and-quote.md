---
name: qualify-and-quote
schedule: daily
skills: [lead-qualification]
tools: [record_list, record_get, record_put, web_fetch, quote_price, catalog_lookup, note_write]
description: Score new leads (BANT), set their status, and create a quote for each qualified lead.
---

Using the lead-qualification skill, process up to 10 leads with status new or nurture that have no quote_id yet. Fetch their evidence pages, score the four dimensions (14+ qualified, 9-13 nurture), set status, choose a pricing model, and call quote_price for every qualified lead and every nurture lead (indicative) at a realistic volume; write the quote_id back onto each lead with record_put. Finish with counts per status and the top three leads with monthly estimates.

## Deliverable
- Every processed lead has score_need, score_budget, score_authority, score_timing, score_total and a status
- Every qualified or nurture lead has a quote_id stored on the lead
- A summary gives counts per status and the top leads with their monthly estimate and pricing model
- Each rationale references something a fetched page said
