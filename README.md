# Agent Foundry

**An agent that builds agents, and the first agent it built sells them.**

Agent Foundry is a course-spec *worker* (responsibility, trigger, tools, limits, reporting) whose standing job is
turning a **commission** into a **verified, packaged, catalogued agent**, each with the same guardrails and its own
Mission Control. Every agent, the Foundry included, runs on one shared runtime, **agentkit**:

| Layer | agentkit gives every agent |
|---|---|
| Context | `SOUL.md`, `AGENTS.md`, `USER.md`, `MEMORY.md` (durable lessons, owner decisions), `skills/**/SKILL.md` (agentskills.io format), `tasks/*.md` with a deliverable checklist |
| Data | a tool registry with a per-agent allowlist: web search + fetch, records, notes, scoped file reads, catalogue, pricing, outreach drafts, proposals |
| Intelligence | a budget-capped model client: local Ollama by default, `claude -p`, or none |
| Automate | Task Scheduler (Windows) or crontab (Linux/macOS), one trigger per agent |
| Build | approval-gated actions, a hash-chained ledger, receipted reports, a doctor, an A2A v1.0 agent card at `/.well-known/agent-card.json`, an `/a2a` endpoint, and Mission Control |

## The Foundry loop (per commission)

```
DEFINE (brief → spec, validated against the runtime, one repair)  >  DESIGN (tools, approvals, limits, port, pricing card)
> GENERATE (package)  >  VERIFY (doctor · tests · real smoke run · ledger · agent card)  >  PACKAGE (zip + sha256 manifest)
> REGISTER (catalogue)  >  PROPOSE (publish / deploy / launch approvals)  >  REPORT  >  HALT
```

Nothing is published, deployed, launched, or sent without the owner clicking Approve.

## The Foundry is also the medic: test and repair broken agents

Every agent on agentkit can be tested three ways, and the Foundry's daily `test-and-repair-agents` task does it for
every catalogued product (shape borrowed from NVIDIA NeMo Evaluator's task → trial → metrics → scores → result bundle):

| Check | What it proves | Command / tab |
|---|---|---|
| **Health** | runs by status, unmet deliverable items, tool error rates, model errors, injection flags, budget, profiler (latency per model step and per tool), doctor, ledger integrity → green / amber / red with reasons | `agentkit --root <agent> health` · Health tab |
| **Evals** (`evals/*.md`) | task-driven trials scored on **outcome** (judge vs. expected checklist), **tool_use** (expected tools called), **efficiency** (steps, seconds), **safety** (no forbidden tools, no secrets); bundle: `run.json`, `trials.jsonl`, `scores.jsonl`, `summary.json`, `report.md` | `agentkit --root <agent> evals` · Health tab → Run evals |
| **Fault injection** | the agent fails *safely*: no model, budget exhausted, tool denied, prompt injection in tool output (canary must not leak), ledger tamper detected, time cap | `agentkit --root <agent> faults` · Health tab → Run fault injection |

Then the Foundry **diagnoses** (skill `agent-diagnosis`: one root-cause class per finding — `spec.task`, `spec.skill`,
`spec.tools`, `spec.limits`, or `env` — with the exact spec field to change) and **repairs** (skill `agent-repair`: a minimal,
validated patch of the commission, proposed as an `apply_fix` approval). On approval it rewrites the commission, rebuilds in
place, re-verifies, re-runs evals and records before/after scores in the Repairs panel. Nothing changes on disk before approval.

Model backends: local Ollama (default), `claude -p`, `none`, or any OpenAI-compatible endpoint including **NVIDIA NIM**
(`[model].backend = "openai_compat"`, key read from the env var named in `openai_api_key_env`, default `NVIDIA_API_KEY`).

## The first product: Agent Seller

Commission `foundry/commissions/001-agent-seller.json` builds `products/agent-seller/`: a sales worker that reads the
catalogue, finds prospects on the public web with evidence, qualifies them (BANT), prices them from 2026 benchmarks
(per-seat, per-task, per-outcome, hybrid, custom build), and drafts outreach that waits in the approvals queue.
Its Mission Control adds Leads, Quotes and Outbox panels.

## Quickstart

Prerequisites: Python 3.11+, `pip install -r requirements.txt`. Optional: Ollama with a model, or the Claude Code CLI.

```bash
python -m agentkit --root foundry doctor          # prerequisites, pass/fail, with the fix for each FAIL
python -m agentkit --root foundry mc              # Foundry Mission Control → http://127.0.0.1:8110  (press Run)
python -m agentkit --root foundry run             # or from the terminal: build every pending commission
python -m agentkit --root foundry approvals list  # publish / deploy / launch proposals for each built agent

python -m agentkit --root products/agent-seller mc        # the Seller's Mission Control → http://127.0.0.1:8111
python -m agentkit --root products/agent-seller run find-prospects --input "agent-seller"
python -m agentkit --root products/agent-seller card       # its A2A agent card

python -m pytest -q                                # hermetic tests; AGENTKIT_LIVE=1 adds the live build + smoke tests
```

Add your own commission: drop a `.json` spec (see `foundry/spec_schema.py::SPEC_TEMPLATE`) or a plain `.md` brief into
`foundry/commissions/` and press Run.

## Layout

```
agentkit/        the runtime: config, brain, store, ledger, model, tools/, worker, approvals, schedule, doctor, report, mc, cli
foundry/         the Foundry agent: agent.toml, core files, skills, tasks, agent.py (pipeline), generator, verifier, spec_schema, commissions/
products/        built agents (agent-seller/ …), catalog.json, dist/ (zips + manifests), published/
tests/           pytest: real temp agents, real SQLite, real FastAPI; live tests behind AGENTKIT_LIVE=1
```

MIT licensed.
