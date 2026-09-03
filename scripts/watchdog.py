#!/usr/bin/env python3
"""Continuous liveness for everything long-running: probes each target on a schedule, heals what has a known safe heal, records every
sample, and turns "ready" into a soak verdict over a window instead of a point-in-time check.

  python scripts/watchdog.py probe [--targets watchdog.json] [--inbox DIR]     # one pass (cron / Task Scheduler runs this)
  python scripts/watchdog.py soak --hours 24 [--min-uptime 0.999]              # READY only if every target met the bar over the window
  python scripts/watchdog.py install [--every 2]                               # cron (Linux/macOS) or schtasks (Windows) entry
  python scripts/watchdog.py report                                            # last sample per target + 24 h uptime

Targets file (JSON list). `url` is probed with GET (expects 2xx, JSON); `cmd` is probed by exit status; `heal` runs once when a probe
fails and the probe is retried after `heal_wait` seconds. Without a targets file the local agents from products/catalog.json and
foundry/agent.toml are probed. Samples: foundry/data/watchdog.jsonl. Failures and heals are also ledger events (`watchdog`)."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
import time
import tomllib
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLES = ROOT / "foundry" / "data" / "watchdog.jsonl"


def default_targets() -> list[dict]:
    t = []
    try:
        port = tomllib.load(open(ROOT / "foundry" / "agent.toml", "rb")).get("mission_control", {}).get("port", 8110)
        t.append({"name": "foundry-mc", "url": f"http://127.0.0.1:{port}/api/status"})
    except OSError:
        pass
    cat = ROOT / "products" / "catalog.json"
    if cat.exists():
        for e in json.loads(cat.read_text(encoding="utf-8")).get("agents", []) if isinstance(json.loads(cat.read_text(encoding="utf-8")), dict) else json.loads(cat.read_text(encoding="utf-8")):
            if e.get("port"):
                t.append({"name": f"{e['slug']}-mc", "url": f"http://127.0.0.1:{e['port']}/api/status"})
    return t


def probe_once(tg: dict) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    try:
        if tg.get("url"):
            with urllib.request.urlopen(tg["url"], timeout=tg.get("timeout", 5)) as r:
                body = r.read(200_000)
                ok = 200 <= r.status < 300
                if ok and tg.get("expect_json", True):
                    json.loads(body)
                return ok, (time.perf_counter() - t0) * 1000, f"HTTP {r.status}"
        p = subprocess.run(tg["cmd"], shell=True, capture_output=True, text=True, timeout=tg.get("timeout", 30))
        return p.returncode == 0, (time.perf_counter() - t0) * 1000, (p.stdout or p.stderr).strip()[:120]
    except Exception as e:  # noqa: BLE001
        return False, (time.perf_counter() - t0) * 1000, f"{type(e).__name__}: {str(e)[:100]}"


def probe(targets: list[dict], inbox: Path | None) -> int:
    from agentkit.ledger import Ledger
    ledger = Ledger(ROOT / "foundry" / "data" / "ledger.jsonl")
    SAMPLES.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    bad = []
    with open(SAMPLES, "a", encoding="utf-8") as f:
        for tg in targets:
            ok, ms, detail = probe_once(tg)
            healed = None
            if not ok and tg.get("heal"):
                subprocess.run(tg["heal"], shell=True, capture_output=True, timeout=120)
                time.sleep(tg.get("heal_wait", 10))
                ok2, ms2, detail2 = probe_once(tg)
                healed = ok2
                ledger.append("watchdog", None, target=tg["name"], what="heal", ok_after=ok2, detail=detail[:120], host=platform.node())
                ok, ms, detail = ok2, ms2, f"healed → {detail2}" if ok2 else f"heal failed → {detail2}"
            row = {"ts": now, "host": platform.node(), "target": tg["name"], "ok": ok, "latency_ms": round(ms, 1), "detail": detail, "healed": healed}
            f.write(json.dumps(row) + "\n")
            print(f"[{'OK ' if ok else 'BAD'}] {tg['name']:<24} {ms:8.1f} ms  {detail}")
            if not ok:
                bad.append(row)
                ledger.append("watchdog", None, target=tg["name"], what="down", detail=detail[:160], host=platform.node())
    if bad and inbox:
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / f"WATCHDOG_ALERT_{platform.node()}.md").write_text(
            f"# Watchdog alert — {platform.node()} — {now}\n\n" + "\n".join(f"- **{b['target']}**: {b['detail']}" for b in bad) + "\n", encoding="utf-8")
    return 1 if bad else 0


def samples(since: datetime) -> list[dict]:
    if not SAMPLES.exists():
        return []
    out = []
    with open(SAMPLES, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                if datetime.fromisoformat(r["ts"]) >= since:
                    out.append(r)
    return out


def soak(hours: float, min_uptime: float, min_samples: int) -> int:
    rows = samples(datetime.now(timezone.utc) - timedelta(hours=hours))
    per: dict[str, list[dict]] = {}
    for r in rows:
        per.setdefault(r["target"], []).append(r)
    if not per:
        print(f"NOT READY: no samples in the last {hours} h — install the watchdog and let it run")
        return 1
    ready = True
    for name, rs in sorted(per.items()):
        up = sum(1 for r in rs if r["ok"]) / len(rs)
        p95 = sorted(r["latency_ms"] for r in rs)[max(0, int(len(rs) * 0.95) - 1)]
        heals = sum(1 for r in rs if r.get("healed") is not None)
        ok = up >= min_uptime and len(rs) >= min_samples
        ready &= ok
        print(f"[{'OK ' if ok else 'BAD'}] {name:<24} uptime {up*100:6.2f}%  samples {len(rs):>5}  p95 {p95:7.1f} ms  heals {heals}")
    print(f"\n{'READY' if ready else 'NOT READY'}: window {hours} h, bar uptime>={min_uptime*100:.2f}% with >={min_samples} samples per target")
    return 0 if ready else 1


def report() -> int:
    rows = samples(datetime.now(timezone.utc) - timedelta(hours=24))
    last: dict[str, dict] = {}
    for r in rows:
        last[r["target"]] = r
    for name, r in sorted(last.items()):
        rs = [x for x in rows if x["target"] == name]
        print(f"{name:<24} last {'OK ' if r['ok'] else 'BAD'} {r['ts']}  24h uptime {100*sum(1 for x in rs if x['ok'])/len(rs):6.2f}% ({len(rs)} samples)  {r['detail'][:60]}")
    return 0


def install(every: int, targets_path: str | None, inbox: str | None) -> int:
    py = sys.executable
    args = f"{shlex.quote(str(ROOT / 'scripts' / 'watchdog.py'))} probe" + (f" --targets {shlex.quote(targets_path)}" if targets_path else "") + (f" --inbox {shlex.quote(inbox)}" if inbox else "")
    if os.name == "nt":
        cmd = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(every), "/TN", "agentkit-watchdog", "/TR", f'"{py}" {args.replace(chr(39), "")}']
        p = subprocess.run(cmd, capture_output=True, text=True)
        print(p.stdout.strip() or p.stderr.strip())
        return p.returncode
    line = f"*/{every} * * * * {shlex.quote(py)} {args} >> {shlex.quote(str(ROOT / 'logs' / 'watchdog.log'))} 2>&1"
    (ROOT / "logs").mkdir(exist_ok=True)
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = [ln for ln in cur.splitlines() if "watchdog.py probe" not in ln]
    p = subprocess.run(["crontab", "-"], input="\n".join(keep + [line]) + "\n", text=True, capture_output=True)
    print("installed:" if p.returncode == 0 else p.stderr, line)
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--targets")
    p.add_argument("--inbox")
    s = sub.add_parser("soak")
    s.add_argument("--hours", type=float, default=24)
    s.add_argument("--min-uptime", type=float, default=0.999)
    s.add_argument("--min-samples", type=int, default=30)
    i = sub.add_parser("install")
    i.add_argument("--every", type=int, default=2)
    i.add_argument("--targets")
    i.add_argument("--inbox")
    sub.add_parser("report")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if a.cmd == "probe":
        targets = json.loads(Path(a.targets).read_text(encoding="utf-8")) if a.targets else default_targets()
        return probe(targets, Path(a.inbox) if a.inbox else None)
    if a.cmd == "soak":
        return soak(a.hours, a.min_uptime, a.min_samples)
    if a.cmd == "install":
        return install(a.every, a.targets, a.inbox)
    return report()


if __name__ == "__main__":
    sys.exit(main())
