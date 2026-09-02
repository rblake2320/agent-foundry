# USER.md — Owner context for Agent Seller

- Audience served: Owners of the catalogue (the Foundry's operator) and, downstream, any company or individual with repetitive work an agent can take over.
- The catalogue is the Foundry's products/catalog.json; every entry has a pricing card and a verified status.
- The Foundry's operator approves every send in Mission Control; drafts wait in the outbox.
- Best-fit buyers: teams with repetitive, rules-based work that already runs on email, spreadsheets, CRMs or public data.
- Reference points the owner cares about: agents that prove they ran (receipts), approval gates, local-model cost control.
- Model: local Ollama first; `claude -p` alternative; reports land in `reports/`.
