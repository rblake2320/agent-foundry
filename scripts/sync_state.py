#!/usr/bin/env python3
"""Keep agent state off any single box. Syncs the Foundry's and every product's data/ (SQLite records, ledger, evidence,
advisories), reports/ and the catalogue to (a) a peer host over rsync/ssh and/or (b) S3 via the aws CLI. Private keys stay
where they were born unless --with-keys is given (then they travel too, so a peer can sign as the same identity after failover).

  python scripts/sync_state.py peer push --host user@standby-box [--jump user@bastion] [--with-keys]
  python scripts/sync_state.py peer pull --host user@standby-box
  python scripts/sync_state.py s3 push --bucket my-bucket [--prefix agent-foundry/<hostname>]
  python scripts/sync_state.py s3 pull --bucket my-bucket --prefix agent-foundry/<other-hostname>
  python scripts/sync_state.py status          # what would be synced, sizes, newest ledger event per agent

Every sync appends a `state_synced` event to the Foundry ledger, so the ledger itself records where its copies are."""
from __future__ import annotations

import argparse
import json
import platform
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
sys.path.insert(0, str(ROOT))

AWS = shutil.which("aws") or shutil.which("aws.cmd") or "aws"   # Windows installs expose aws.cmd
STATE_GLOBS = ["foundry/data", "foundry/reports", "products/catalog.json", "products/*/data", "products/*/reports"]


def state_dirs() -> list[Path]:
    out: list[Path] = []
    for g in STATE_GLOBS:
        out += [p for p in ROOT.glob(g) if p.exists()]
    return sorted(set(out))


def relpaths() -> list[str]:
    return [p.relative_to(ROOT).as_posix() for p in state_dirs()]


def _size(p: Path) -> int:
    return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def status() -> dict:
    rows = []
    for p in state_dirs():
        led = p / "ledger.jsonl"
        last = None
        if led.exists():
            lines = [ln for ln in led.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                last = {"ts": last["ts"], "event": last["event"], "hash": last["hash"][:12]}
        rows.append({"path": p.relative_to(ROOT).as_posix(), "bytes": _size(p), "ledger_head": last,
                     "keys": (p / "keys").exists(), "evidence_bundles": len(list((p / "evidence").glob("*"))) if (p / "evidence").exists() else 0})
    return {"root": str(ROOT), "host": platform.node(), "items": rows, "total_bytes": sum(r["bytes"] for r in rows)}


def _run(cmd: list[str], dry: bool) -> int:
    print("$", " ".join(shlex.quote(c) for c in cmd))
    if dry:
        return 0
    return subprocess.call(cmd)


def peer(direction: str, host: str, jump: str | None, with_keys: bool, dry: bool, remote_root: str) -> int:
    ssh = "ssh" + (f" -J {jump}" if jump else "")
    rc = 0
    if not shutil.which("rsync"):   # e.g. Windows: one tar stream over ssh instead of rsync (whole state, still excludes keys)
        rels = relpaths()
        excl = [] if with_keys else ["--exclude=keys", "--exclude=*/keys/*"]
        if direction == "push":
            cmd = f"tar -C {shlex.quote(ROOT.as_posix())} {' '.join(excl)} -cf - {' '.join(shlex.quote(r) for r in rels)} | {ssh} {shlex.quote(host)} {shlex.quote(f'mkdir -p {remote_root} && tar -xf - -C {remote_root}')}"
        else:
            cmd = f"{ssh} {shlex.quote(host)} {shlex.quote(f'tar -C {remote_root} ' + ' '.join(excl) + ' -cf - ' + ' '.join(rels))} | tar -xf - -C {shlex.quote(ROOT.as_posix())}"
        print("$", cmd)
        return 0 if dry else subprocess.call(cmd, shell=True)
    for rel in relpaths():
        src, dst = (f"{ROOT.as_posix()}/{rel}", f"{host}:{remote_root}/{rel}") if direction == "push" else (f"{host}:{remote_root}/{rel}", f"{ROOT.as_posix()}/{rel}")
        is_dir = (ROOT / rel).is_dir() if direction == "push" else not rel.endswith(".json")
        if is_dir:
            src, dst = src + "/", dst + "/"
        cmd = ["rsync", "-az", "--mkpath", "-e", ssh] + ([] if with_keys else ["--exclude", "keys/"]) + [src, dst]
        rc |= _run(cmd, dry)
    return rc


def s3(direction: str, bucket: str, prefix: str, with_keys: bool, dry: bool) -> int:
    rc = 0
    for rel in relpaths():
        local, remote = str(ROOT / rel), f"s3://{bucket}/{prefix}/{rel}"
        if (ROOT / rel).is_file() or rel.endswith(".json"):
            cmd = [AWS, "s3", "cp"] + ([local, remote] if direction == "push" else [remote, local])
        else:
            cmd = [AWS, "s3", "sync"] + ([local, remote] if direction == "push" else [remote, local]) + ([] if with_keys else ["--exclude", "keys/*"])
        rc |= _run(cmd, dry)
    return rc


def record(kind: str, target: str, direction: str, rc: int, with_keys: bool) -> None:
    try:
        from agentkit.ledger import Ledger
        Ledger(ROOT / "foundry" / "data" / "ledger.jsonl").append("state_synced", None, kind=kind, target=target, direction=direction,
                                                                  ok=rc == 0, with_keys=with_keys, host=platform.node(), items=len(relpaths()))
    except Exception as e:  # noqa: BLE001
        print(f"(ledger not updated: {e})")
    (ROOT / "foundry" / "data" / "sync_state.json").write_text(json.dumps(
        {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "target": target, "direction": direction, "ok": rc == 0,
         "with_keys": with_keys, "items": relpaths()}, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("peer")
    p.add_argument("direction", choices=["push", "pull"])
    p.add_argument("--host", required=True)
    p.add_argument("--jump")
    p.add_argument("--remote-root", default="~/agent-foundry")
    p.add_argument("--with-keys", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("s3")
    s.add_argument("direction", choices=["push", "pull"])
    s.add_argument("--bucket", required=True)
    s.add_argument("--prefix", default=f"agent-foundry/{platform.node()}")
    s.add_argument("--with-keys", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.mode == "status":
        print(json.dumps(status(), indent=2))
        return 0
    t0 = time.time()
    if a.mode == "peer":
        rc = peer(a.direction, a.host, a.jump, a.with_keys, a.dry_run, a.remote_root)
        target = a.host
    else:
        rc = s3(a.direction, a.bucket, a.prefix, a.with_keys, a.dry_run)
        target = f"s3://{a.bucket}/{a.prefix}"
    if not a.dry_run:
        record(a.mode, target, a.direction, rc, a.with_keys)
    print(f"{a.mode} {a.direction} -> {target}: {'OK' if rc == 0 else 'FAILED'} ({len(relpaths())} items, {round(time.time() - t0, 1)}s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
