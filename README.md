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

## Runtime below the harness: NVIDIA OpenShell

agentkit governs what an agent *tries* (tool allowlist, approvals, budgets). NVIDIA OpenShell governs what it *can do*
(Landlock filesystem, seccomp, proxy-enforced egress, routed inference, injected credentials). Every agent exports a
deny-by-default OpenShell sandbox policy derived from its own allowlist, plus a launch doc:

```bash
python -m agentkit --root products/agent-seller openshell     # writes openshell/policy.yaml + RUN_UNDER_OPENSHELL.md
```

Only hosts a tool actually needs are allowed (web search → DuckDuckGo, read-only, python binary only); model calls go
through `inference.local` so caller keys are stripped; approval-gated actions such as `send_email` are **not**
pre-authorized and get a narrow rule only when the owner approves. The Foundry's own policy has no tool egress at all,
so a built agent never holds more authority than its builder (the "authority ceiling" OpenShell enforces for subagents).
Mission Control shows the policy at `/api/openshell`.

## Trust layer: born certified, approvals as mandates, agent recall

Every agentkit agent (the Foundry and everything it builds) carries its own trust material, generated on first use under
`data/keys/` and never leaving the box unless the owner syncs it:

- **Identity.** An Ed25519 key and a self-certifying `did:agentkit:<slug>:<hash>`, plus a P-256 key for mandates. Both public
  keys are published in the A2A agent card (`capabilities.extensions[urn:agentkit:identity:1]`).
- **Evidence at birth.** `agentkit --root X evidence build` (or the Evidence tab, or the Foundry's EVIDENCE phase right after
  PACKAGE) writes a signed bundle under `data/evidence/<stamp>/`: `manifest.json` + `agent-manifest.yaml` (identity; authority =
  tools, approval actions, limits; build provenance = spec, core-file and skill hashes; OpenShell policy hash; verification =
  health, evals, fault injection; ledger checkpoint), the attached eval/fault artifacts, and `signature.json` over every file
  hash. `evidence verify <dir>` checks a bundle offline with nothing but the public key. The format is `agentkit-evidence/1`:
  ours and documented, not a claim of conformance to any third-party certification scheme.
- **Approvals as mandates.** When the owner approves an action (Mission Control, CLI or `/api/approvals/{id}/approve`) the
  approval is issued as an SD-JWT signed with the agent's P-256 key (ES256): selectively-disclosable claims (action, target,
  payload hash, approver, rationale, approval id), `cnf` key binding, `iat`/`exp`. It is shaped after AP2 mandates so a
  payments or trust rail can consume it, and verifiable offline via `agentkit mandate <id>` or `/api/mandates/{id}`. It has not
  been interop-tested against a live AP2 credential provider; that is stated plainly rather than implied.
- **Agent recall.** Revocation standards stop at "the action was logged"; recall answers what happened to the *work*. Every
  record carries the run that created it and links to related records by id; approvals point at their targets.
  `agentkit recall impact --type run|record|approval|model|task|skill <seed>` previews everything reachable;
  `recall issue --reason ...` quarantines those records (status becomes `recalled`, the prior status is kept), denies dependent
  pending/approved approvals and writes a signed advisory under `data/advisories/` (ledger event `recall_issued`);
  `recall lift <advisory>` restores the records (denied approvals stay denied). Reachability is deliberately conservative
  (id-linked records are one unit of work) because the owner previews before issuing and lift is cheap.

## No single point of failure

- **Inference.** `[model.fallback]` in `agent.toml` names a second backend (another Ollama host, a cloud NIM, ...). If the primary is
  unreachable or errors mid-run, the client fails over for the rest of the run and the receipt records `model_failovers` and why.
- **Runtime.** `scripts/spark_bootstrap.sh` rebuilds the OpenShell gateway, provider, inference route, policy, sandbox and
  port-forward on a fresh Linux box from zero, idempotently (`--check` audits, `--recreate` rebuilds the sandbox from current
  code). Run it on the standby box and the agent comes up there.
- **State.** `scripts/sync_state.py` pushes or pulls every agent's `data/` (records, ledger, evidence, advisories), `reports/`
  and the catalogue to a peer over rsync/ssh or to S3 (`status` shows what and how fresh). Keys stay put unless `--with-keys`.
  Every sync is itself a ledger event (`state_synced`), so the ledger records where its own copies are.

## Readiness is declared, measured and soaked — never assumed

- **Load.** `python scripts/make_load_fixture.py products/agent-seller <dir>` builds a production-sized state (50k hash-chained ledger
  events, 20k records, 2k runs, 500 approvals); `python scripts/load_test.py --base http://127.0.0.1:8111 --procs 8` drives it with a
  multi-process generator and prints requests/s, p50/p95/p99 and error rate per endpoint and concurrency level against a stated bar.
  Measured 2026-09-03 on one Linux box (20-core DGX Spark, one uvicorn worker, SQLite WAL): 100 concurrent readers at 3–10k req/s with
  p95 ≤ 70 ms and 0 errors; 500 concurrent readers with 0 errors and p95 0.5–6 s (degraded, not broken). That is the verified tier for
  *one agent instance*: Pilot-ready and Department-ready for readers. Sustained (hours) and write-mix runs are not yet done; multi-tenant
  surfaces do not exist. `tests/test_scale.py` pins the O(1) ledger, SQL-side filters and hot-path latency so they cannot regress.
- **Liveness.** `python scripts/watchdog.py install --every 2 --targets watchdog.json` probes every long-running piece from cron or Task
  Scheduler, heals what has a safe heal (e.g. restart the sandbox port-forward), records every sample and writes ledger events;
  `watchdog.py soak --hours 24` is the readiness verdict. Long-running processes run under systemd (`Restart=always`), never `nohup &`.
- **Fault injection below the harness.** `scripts/infra_faults.sh forward gateway sandbox` breaks the runtime for real (kill the forward,
  restart the gateway, stop/start the sandbox) and records recovery time in the ledger. Agent-level fault injection stays in `agentkit faults`.
- **Serving.** `agentkit mc --workers N` scales reads across processes on Linux (run state is in the store, so one run at a time is still
  enforced); reads are served from a 1 s cache of serialized bodies with single-flight recompute and write invalidation; overload sheds
  fast with HTTP 503 (`AGENTKIT_MC_LIMIT_CONCURRENCY`, default 512 per worker). Do not use `--workers > 1` on Windows.

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
