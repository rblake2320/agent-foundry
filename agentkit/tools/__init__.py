"""Tool registry. Every tool: name, description, args (name -> description), risk (read|write|external),
and fn(ctx, **args) -> str. Output is DATA: the worker wraps it as UNTRUSTED before the model sees it.
An agent only gets the tools listed in its agent.toml [tools].allowed."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    args: dict[str, str]
    fn: Callable
    risk: str = "read"              # read | write | external
    approval_action: str | None = None   # if set, the tool only PROPOSES this action (never executes)


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args: dict[str, str], risk: str = "read", approval_action: str | None = None):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, args, fn, risk, approval_action)
        return fn
    return deco


class ToolContext:
    """What a tool may touch: the agent config, its store, its ledger, the current run id and task."""
    def __init__(self, cfg, store, ledger, run_id: str | None = None, task: str | None = None):
        self.cfg, self.store, self.ledger, self.run_id, self.task = cfg, store, ledger, run_id, task
        self.proposed: list[int] = []


def allowed_tools(cfg) -> dict[str, Tool]:
    return {n: REGISTRY[n] for n in cfg.tools_allowed if n in REGISTRY}


def describe(tools: dict[str, Tool]) -> str:
    lines = []
    for t in tools.values():
        a = ", ".join(f"{k}: {v}" for k, v in t.args.items()) or "no arguments"
        gate = f" [proposes approval '{t.approval_action}', never executes]" if t.approval_action else ""
        lines.append(f"- {t.name} ({t.risk}){gate}: {t.description} Args: {a}")
    return "\n".join(lines)


def run_tool(ctx: ToolContext, name: str, args: dict, tools: dict[str, Tool], max_chars: int) -> str:
    if name not in tools:
        return f"ERROR: tool '{name}' is not in this agent's allowlist ({', '.join(tools)})"
    t = tools[name]
    try:
        out = t.fn(ctx, **{k: v for k, v in (args or {}).items() if k in t.args})
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:  # noqa: BLE001 — a tool failure is data for the model, not a crash
        return f"ERROR: {name} failed: {type(e).__name__}: {str(e)[:300]}"
    out = str(out)
    return out if len(out) <= max_chars else out[:max_chars] + f"\n…[truncated {len(out) - max_chars} chars]"


# register built-ins
from . import records, sales, web  # noqa: E402,F401
