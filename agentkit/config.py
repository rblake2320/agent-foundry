"""agent.toml loader. Every agent (the Foundry and everything it builds) is a folder with:
agent.toml, SOUL.md, AGENTS.md, USER.md, MEMORY.md, skills/**/SKILL.md, tasks/*.md, data/, reports/."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentMeta:
    name: str = "Unnamed Agent"
    slug: str = "unnamed-agent"
    version: str = "0.1.0"
    description: str = ""
    organization: str = "Agent Foundry"
    responsibility: str = ""
    audience: str = ""


@dataclass
class ModelCfg:
    backend: str = "ollama"          # ollama | claude | openai_compat (NVIDIA NIM, vLLM, any OpenAI-style endpoint) | none
    ollama_model: str = "qwen3.8:27b"
    ollama_url: str = "http://localhost:11434"
    ollama_num_ctx: int = 16384
    claude_model: str = "sonnet"
    openai_base_url: str = "https://integrate.api.nvidia.com/v1"   # NVIDIA NIM cloud by default; point at a local NIM for on-prem
    openai_model: str = "nvidia/nemotron-3-super-120b-a12b"
    openai_api_key_env: str = "NVIDIA_API_KEY"                      # the key is read from this env var, never from the file
    fallback: "ModelCfg | None" = None                              # [model.fallback]: takes over for the rest of a run if the primary fails


@dataclass
class Limits:
    max_model_calls_per_run: int = 80
    max_tokens_per_run: int = 400_000
    max_run_minutes: int = 60
    monthly_model_call_cap: int = 3000
    max_steps_per_task: int = 12
    max_tool_calls_per_task: int = 20
    tool_output_chars: int = 6000


@dataclass
class Config:
    root: Path
    agent: AgentMeta
    model: ModelCfg
    limits: Limits
    tools_allowed: list[str]
    approval_actions: list[str]
    schedule_time: str
    inbox: Path
    db: Path
    ledger: Path
    mc_host: str
    mc_port: int
    extra: dict = field(default_factory=dict)   # [agent_data] free-form settings the agent's tools may read

    @property
    def core_files(self) -> dict[str, Path]:
        return {n: self.root / f"{n}.md" for n in ("SOUL", "AGENTS", "USER", "MEMORY")}

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def data_dir(self) -> Path:
        return self.db.parent

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"


def _p(root: Path, s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else root / p


def _dc(cls, raw: dict):
    return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def load(root: Path | str) -> Config:
    root = Path(root).resolve()
    path = root / "agent.toml"
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    mraw = dict(raw.get("model", {}))
    fb = mraw.pop("fallback", None)
    model = _dc(ModelCfg, mraw)
    if isinstance(fb, dict) and fb.get("backend", "none") != "none":
        model.fallback = _dc(ModelCfg, fb)
    if os.environ.get("AGENTKIT_MODEL_BACKEND"):
        model.backend = os.environ["AGENTKIT_MODEL_BACKEND"]
    if os.environ.get("AGENTKIT_OPENAI_BASE_URL"):        # e.g. https://inference.local/v1 under OpenShell (routed, key-injected inference)
        model.openai_base_url = os.environ["AGENTKIT_OPENAI_BASE_URL"]
    paths = raw.get("paths", {})
    mc = raw.get("mission_control", {})
    return Config(
        root=root,
        agent=_dc(AgentMeta, raw.get("agent", {})),
        model=model,
        limits=_dc(Limits, raw.get("limits", {})),
        tools_allowed=list(raw.get("tools", {}).get("allowed", [])),
        approval_actions=list(raw.get("approvals", {}).get("actions", [])),
        schedule_time=str(raw.get("schedule", {}).get("time", "07:30")),
        inbox=_p(root, paths.get("inbox", "reports")),
        db=_p(root, paths.get("db", "data/agent.db")),
        ledger=_p(root, paths.get("ledger", "data/ledger.jsonl")),
        mc_host=mc.get("host", "127.0.0.1"),
        mc_port=int(mc.get("port", 8110)),
        extra=dict(raw.get("agent_data", {})),
    )
