# Agent Seller

A sales worker that knows every agent in the Foundry catalogue, finds and qualifies the companies and people who could use one, prices it with 2026 market benchmarks, and drafts outreach and quotes that never go out without the owner's approval.

- **Responsibility:** Turn the catalogue into a qualified, priced, approval-gated sales pipeline: find prospects, qualify them, quote them, and draft the outreach, without ever contacting anyone on its own.
- **Audience:** Owners of the catalogue (the Foundry's operator) and, downstream, any company or individual with repetitive work an agent can take over.
- **Trigger:** daily at 08:00
- **Tools:** catalog_lookup, web_search, web_fetch, record_put, record_get, record_list, quote_price, draft_outreach, note_write, current_time
- **Approval actions:** send_email, schedule_call
- **Pricing:** hybrid — 249.0 per month base (5 seats) + $0.20 per qualified lead (Hybrid is where the 2026 market is converging: a predictable base for procurement plus a usage component that aligns cost to value; per-outcome on qualified leads mirrors HubSpot Breeze's $1.00/lead benchmark at a fraction, because the owner still approves every send.)

Built by Agent Foundry on agentkit. Run from the foundry repo root:

```
python -m agentkit --root products/agent-seller doctor
python -m agentkit --root products/agent-seller run <task>
python -m agentkit --root products/agent-seller mc          # Mission Control on port 8111
```
Agent card: `http://127.0.0.1:8111/.well-known/agent-card.json`
