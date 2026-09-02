---
name: find-prospects
schedule: daily
skills: [prospect-discovery]
tools: [catalog_lookup, web_search, web_fetch, record_put, record_list, note_write, current_time]
description: Find up to 5 new prospects for one catalogue agent from public sources and record them as leads with evidence.
---

Pick the catalogue agent named in the owner's input, or the first verified agent in the catalogue if none is given. Using the prospect-discovery skill, find up to 5 named companies that visibly pay for, or do by hand, the work that agent automates: companies hiring for the role, or small firms whose own site shows the manual process (quote forms, intake forms, 'call us for pricing'). Use plain queries without quotes or operators. Fetch the company's own page, then record the lead immediately with record_put. Skip anything already in the leads collection (check with record_list first). Finish with a short summary listing each lead's company, why_fit and evidence_url, and what was skipped.

## Deliverable
- The target catalogue agent is named and its card was read with catalog_lookup
- Between 1 and 5 new leads were recorded, each with company, why_fit, evidence_url, agent_slug, score and status new
- Each recorded lead cites a page that was actually fetched
- A summary lists every lead with its evidence URL and states how many candidates were skipped and why
