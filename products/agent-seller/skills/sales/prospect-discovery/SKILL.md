---
name: prospect-discovery
description: Find companies and people who could use a specific catalogue agent, from public web sources, and record them as leads with evidence. Load for any task that needs new prospects.
---

# Prospect Discovery

## When to Use
- A task asks for new prospects for a catalogue agent.
- NOT for qualification or outreach; those have their own skills.

## Quick Reference
- Hunt where the buyer's job is visible, not where people talk about it. Three query families that work: (1) job postings for the role the agent replaces, e.g. 'sales development representative job [city]' or 'lead qualification specialist hiring' — the HIRING COMPANY is the lead; (2) small firms in trades that quote by hand: 'request a quote' HVAC contractor, freight broker, print shop, staffing agency, commercial cleaning — their own site is the evidence; (3) local directories or association member lists, then fetch individual member sites.
- Use plain-language queries of 4-8 words. No quotation marks, no OR/AND operators: the search engine ignores them and returns vendor blogs.
- Vendor blogs, how-to articles, LinkedIn posts and anonymized case studies are NOT leads. A lead is a named company with its own page.
- Record a lead IMMEDIATELY after one fetched page shows the repetitive work (a quote form, a manual intake process, a hiring post for the role). Do not batch; do not wait for perfect evidence.
- A lead record needs: company, why_fit (one concrete sentence quoting what the page shows), evidence_url, agent_slug, source_query, status=new, score (1-5).
- Never record personal emails; record the company's public contact page URL if one exists.
- Budget: at most 5 searches and 6 fetches per run; if a search returns only vendors, switch query family instead of rephrasing.

## Procedure
1. Call catalog_lookup to read the target agent's card and pricing; call record_list on leads to avoid duplicates.
2. Run one query from family (1) job postings and one from family (2) trades that quote by hand; add a family (3) directory query only if the first two gave fewer than 3 candidates.
3. For each named company in the results, web_fetch its own page (not the job board's home page) and extract what they do and the concrete repetitive work.
4. The moment a fetched page shows the work, record_put the lead into collection 'leads' with the required fields and status 'new'; then move to the next candidate.
5. Stop at 5 leads, or when 5 searches and 6 fetches are used; summarize what was recorded and what was skipped, with reasons.

## Pitfalls
- Recording a lead from a snippet alone (no fetched page) is a violation.
- Spending the whole budget searching and never recording is the failure mode to avoid: switch families early, record early.
- Directories, job boards and aggregators are sources of names, not leads themselves; the named company is the lead.
- Do not record competitors of the catalogue (sales-automation or agent vendors) as prospects.

## Verification
- [ ] Every lead has why_fit, evidence_url and agent_slug.
- [ ] Every lead's evidence_url was fetched during the task.
- [ ] No personal email addresses stored.
