"""Budget-capped model client with failover. Primary backend: Ollama (local), `claude -p`, or any OpenAI-compatible endpoint
(NVIDIA NIM cloud/local, vLLM). Optional `[model.fallback]` (a second Ollama host, a cloud NIM…) takes over for the rest of the
run when the primary is unreachable or errors, so one dead box never ends a run; the receipt records the failover.
Caching habit: stable prefix first (system), variable data last (user)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from .config import Config, ModelCfg
from .store import Store

_LIVE = ("ollama", "claude", "openai_compat")


class BudgetExceeded(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


def model_name(m: ModelCfg) -> str:
    return {"ollama": m.ollama_model, "claude": f"claude:{m.claude_model}", "openai_compat": f"openai_compat:{m.openai_model}"}.get(m.backend, "none")


class ModelClient:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.calls = self.tokens_in = self.tokens_out = 0
        self.primary = cfg.model
        self.failovers = 0
        self.failover_reason: str | None = None
        self.last_latency_s = 0.0
        self._activate(cfg.model)

    def _activate(self, m: ModelCfg) -> None:
        self.active, self.backend, self.name = m, m.backend, model_name(m)

    @property
    def available(self) -> bool:
        fb = self.primary.fallback
        return self.backend in _LIVE or bool(fb and fb.backend in _LIVE)

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
        return {"backend": self.backend, "model": self.name, "primary": model_name(self.primary), "calls": self.calls,
                "tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "failovers": self.failovers, "failover_reason": self.failover_reason}

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
        try:
            text, tin, tout = self._dispatch(system, user, json_mode)
        except ModelError as e:
            fb = self.primary.fallback
            if self.active is self.primary and self.backend in _LIVE and fb and fb.backend in _LIVE:
                self.failovers += 1
                self.failover_reason = f"{model_name(self.primary)}: {str(e)[:200]}"
                self._activate(fb)
                text, tin, tout = self._dispatch(system, user, json_mode)
            else:
                raise
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.store.add_budget(1, tin, tout)
        self.last_latency_s = round(time.time() - t0, 1)
        return text

    def _dispatch(self, system: str, user: str, json_mode: bool):
        if self.backend == "ollama":
            return self._ollama(self.active, system, user, json_mode)
        if self.backend == "claude":
            return self._claude(self.active, system, user)
        if self.backend == "openai_compat":
            return self._openai_compat(self.active, system, user, json_mode)
        if self.backend == "none":
            raise ModelError("no model configured ([model].backend = none)")
        raise ModelError(f"unknown backend {self.backend}")

    def _ollama(self, m: ModelCfg, system: str, user: str, json_mode: bool):
        body = {"model": m.ollama_model, "stream": False, "options": {"temperature": 0, "num_ctx": m.ollama_num_ctx},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["format"] = "json"
        req = urllib.request.Request(m.ollama_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise ModelError(f"ollama HTTP {e.code}: {e.read(300).decode('utf-8', errors='replace')}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ModelError(f"ollama unreachable ({m.ollama_url}): {e}") from e
        return data.get("message", {}).get("content", ""), int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))

    def _openai_compat(self, m: ModelCfg, system: str, user: str, json_mode: bool):
        """OpenAI-style /chat/completions: NVIDIA NIM (cloud or local), vLLM, LM Studio… Key from env only."""
        key = os.environ.get(m.openai_api_key_env, "")
        if not key and not any(h in m.openai_base_url for h in ("localhost", "127.0.0.1", "inference.local")):
            raise ModelError(f"env var {m.openai_api_key_env} is not set")
        body = {"model": m.openai_model, "temperature": 0, "max_tokens": 2048,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        def call(b: dict, timeout: int):
            req = urllib.request.Request(m.openai_base_url.rstrip("/") + "/chat/completions", data=json.dumps(b).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        try:
            try:
                data = call(body, 180)
            except urllib.error.HTTPError as e0:
                if e0.code in (429, 503):  # overloaded endpoint: one retry after a short pause, then report honestly
                    time.sleep(4)
                    data = call(body, 180)
                else:
                    raise
        except urllib.error.HTTPError as e:
            detail = e.read(500).decode("utf-8", errors="replace")
            if json_mode and e.code == 400 and "response_format" in detail:
                body.pop("response_format", None)  # some NIMs reject json_object; retry plain
                try:
                    data = call(body, 600)
                except (urllib.error.URLError, TimeoutError, OSError) as e2:
                    raise ModelError(f"openai_compat failed: {e2}") from e2
            else:
                raise ModelError(f"openai_compat HTTP {e.code}: {detail[:200]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ModelError(f"openai_compat unreachable ({m.openai_base_url}): {e}") from e
        u = data.get("usage") or {}
        return (data["choices"][0]["message"].get("content") or ""), int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))

    def _claude(self, m: ModelCfg, system: str, user: str):
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        try:
            p = subprocess.run(["claude", "-p", "--model", m.claude_model, "--output-format", "json", "--system-prompt", system, user],
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
