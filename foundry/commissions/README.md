# Commissions

Drop a commission here and the Foundry builds it on its next run.

- `NNN-name.json` — spec-first: a complete agent spec (see `spec_schema.py::SPEC_TEMPLATE`). Deterministic build.
- `NNN-name.md` — brief-first: a plain-language brief (`# Title` + what the agent must do, for whom, what must never happen).
  The Foundry derives the spec with the `agent-commission` skill, validates it against the runtime, repairs once, then builds.

Each commission ends as **built** (verification PASS, catalogued, approvals proposed), **failed** (first failing gate recorded),
or **pending** (budget/time cap). Re-queue from the Commissions panel in Mission Control.
