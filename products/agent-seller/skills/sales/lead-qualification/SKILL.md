---
name: lead-qualification
description: Qualify recorded leads with a BANT-style score (need, budget signal, authority path, timing) from public evidence, and pick the right pricing model. Load for qualification and quoting tasks.
---

# Lead Qualification

## When to Use
- Leads exist with status new and need a decision.
- NOT for discovery or outreach.

## Quick Reference
- Score 1-5 on each: Need (is the repetitive work real and frequent?), Budget signal (company size, hiring, paid tools in use), Authority (is there an obvious owner of the process?), Timing (a posting, a launch, a seasonal peak).
- Total 14-20 = qualified; 9-13 = nurture; <=8 = disqualified with a reason. Web evidence rarely shows budget or authority directly: score 3 when the company is clearly operating and hiring, do not default to 1.
- Store scores as score_need, score_budget, score_authority, score_timing and score_total on the lead.
- Quote every qualified lead, and give every nurture lead an INDICATIVE quote (same tool, note 'indicative' in the rationale) so the owner sees the value at stake; disqualified leads get no quote.
- Pricing model choice: enterprise or procurement-heavy buyer -> hybrid (base + usage); small team -> per_seat; well-defined countable outcome (tickets, leads, invoices) -> per_outcome; unique workflow -> custom_build.
- 2026 benchmarks: $20-100/user/month per-seat; $0.05-0.50 per task; ~$0.99 per verified outcome ($0.50-2.00 range); custom builds $20k-300k+.

## Procedure
1. record_list leads with status new (limit 10).
2. For each, web_fetch the evidence_url (and the company's home page if known) and score the four BANT dimensions from what the pages actually say.
3. Update the lead with record_put: scores, total, status (qualified | nurture | disqualified), recommended_pricing_model, and a one-sentence rationale.
4. For each qualified or nurture lead, call quote_price with the recommended model, a realistic volume, and the mid tier (nurture = indicative); store the returned quote id on the lead as quote_id.
5. Summarize: counts per status, the top three leads with their monthly estimate and pricing model.

## Pitfalls
- Scoring from the company name alone.
- Quoting a disqualified lead.
- Choosing per_outcome when the outcome is not countable.
- Forgetting to write quote_id back onto the lead.

## Verification
- [ ] Every processed lead has four scores, a total and a status.
- [ ] Every qualified or nurture lead has a quote_id.
- [ ] Rationales cite what a fetched page said.
