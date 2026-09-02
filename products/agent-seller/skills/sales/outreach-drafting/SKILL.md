---
name: outreach-drafting
description: Write short, specific outreach for qualified leads and propose it for approval; never send. Load for outreach tasks.
---

# Outreach Drafting

## When to Use
- Qualified leads with a quote and no draft yet, plus nurture leads with score_total of 12 or more (light-touch draft: shorter, no price, one question).
- NOT for cold lists, disqualified leads, or nurture leads under 12.

## Quick Reference
- Structure (under 130 words): the specific repetitive work you saw (with where you saw it), what the agent does about it in one sentence, the proof (it runs on a schedule, reports with receipts, every external action needs their approval), the price model in one line, one low-friction ask (a 15-minute look at the Mission Control).
- Recipient: the company's public contact address if the fetched page lists one; otherwise 'unknown' so the owner fills it in.
- Subject: the work, not the product: 'Your weekly X, done by an agent you approve'.
- One draft per lead; mark the lead status 'drafted' with the draft id.

## Procedure
1. record_list leads with status qualified (limit 5); then record_list leads with status nurture and keep those with score_total >= 12 and no draft_id.
2. For each, record_get the quote by quote_id (collection 'quotes') and re-read why_fit and evidence_url.
3. Write the draft following the structure and call draft_outreach; this creates the approval.
4. Update the lead with record_put: status drafted, draft_id, approval_id.
5. Summarize the drafts created and remind the owner they are waiting for approval.

## Pitfalls
- Claiming results the agent has not produced.
- Generic openers; if the first sentence could go to anyone, rewrite it.
- Sending is impossible by design; do not describe a draft as sent.

## Verification
- [ ] Every draft is under 130 words and names the specific work seen.
- [ ] Every draft has a pending send_email approval.
- [ ] No lead was drafted twice.
