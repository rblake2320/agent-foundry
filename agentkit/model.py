"""Budget-capped model client: Ollama (local, default), `claude -p`, or none.
Caching habit: stable prefix first (system), variable data last (user)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from .config import Config
from .store import Store


class BudgetExceeded(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


class ModelClient:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.calls = self.tokens_in = self.tokens_out = 0
        self.backend = cfg.model.backend
        self.name = {"ollama": cfg.model.ollama_model, "claude": f"claude:{cfg.model.claude_model}"}.get(self.backend, "none")
        self.last_latency_s = 0.0

    @property
    def available(self) -> bool:
        return self.backend in ("ollama", "claude")

    def check_budget(self) -> None:
        lim = self.cfg.limits
        if self.calls >= lim.max_model_calls_per_run:
            raise BudgetExceeded(f"run cap reached: {self.calls} model calls")
        if self.tokens_in + self.tokens_out >= lim.max_tokens_per_run:
            raise BudgetExceeded(f"run token cap reached: {self.tokens_in + self.tokens_out}")
        m = self.store.month_budget()
        if m["model_calls"] >= lim.monthly_model_call_cap:
            raise BudgetExceeded(f"monthly cap reached: {m['model_calls']} calls in {m['month']}")

    def usage(self) -> dict:
        return {"backend": self.backend, "model": self.name, "calls": self.calls, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out}

    def complete_json(self, system: str, user: str, retries: int = 1) -> dict:
        last = None
        for _ in range(retries + 1):
            text = self.complete(system, user, json_mode=True)
            obj = extract_json(text)
            if obj is not None:
                return obj
            last = text[:300]
            user += "\n\nReturn ONLY one JSON object. No prose, no markdown fences."
        raise ModelError(f"model did not return JSON: {last}")

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        self.check_budget()
        t0 = time.time()
        if self.backend == "ollama":
            text, tin, tout = self._ollama(system, user, json_mode)
        elif self.backend == "claude":
            text, tin, tout = self._claude(system, user)
        elif self.backend == "none":
            raise ModelError("no model configured ([model].backend = none)")
        else:
            raise ModelError(f"unknown backend {self.backend}")
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.store.add_budget(1, tin, tout)
        self.last_latency_s = round(time.time() - t0, 1)
        return text

    def _ollama(self, system: str, user: str, json_mode: bool):
        body = {"model": self.cfg.model.ollama_model, "stream": False,
                "options": {"temperature": 0, "num_ctx": self.cfg.model.ollama_num_ctx},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["format"] = "json"
        req = urllib.request.Request(self.cfg.model.ollama_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                data = json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ModelError(f"ollama unreachable: {e}") from e
        return data.get("message", {}).get("content", ""), int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))

    def _claude(self, system: str, user: str):
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        try:
            p = subprocess.run(["claude", "-p", "--model", self.cfg.model.claude_model, "--output-format", "json", "--system-prompt", system, user],
                               capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace", env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ModelError(f"claude -p failed: {e}") from e
        if p.returncode != 0:
            raise ModelError(f"claude -p rc={p.returncode}: {p.stderr[:300]}")
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.stdout, 0, 0
        u = data.get("usage", {}) or {}
        return str(data.get("result", "")), int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))


def extract_json(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text.strip(), flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None
