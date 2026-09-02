# USER.md — Owner context for the Agent Foundry

- The owner is building a catalogue of sellable worker agents. The first commission is the
  Agent Seller, which sells everything else in the catalogue.
- Owned and free infrastructure first: the local Ollama model is the default for the Foundry and
  for every agent it builds; `claude -p` is the alternative; `none` still lets an agent boot.
- Every built agent must be demonstrable in under a minute: its Mission Control opens, a task
  runs, a receipt appears, an approval waits for a human.
- Deliverables (reports, packages) land in `products/` and the reports folder.
- Timezone: America/Chicago.
