"""End-to-end: boot a real Mission Control for a temp agent, drive it with a real browser, click every tab
(including a records panel), deny an approval, edit a brain file, run a task, and fail on ANY console error or 4xx/5xx."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from agentkit.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AGENT_PY = '''from agentkit.worker import Worker  # noqa: F401
PANELS = [{"name": "Leads", "collection": "leads", "columns": ["company", "status"], "actions": []}]
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mc(cfg):
    (cfg.root / "agent.py").write_text(AGENT_PY, encoding="utf-8")
    store = Store(cfg.db)
    store.put("leads", "acme", {"company": "Acme", "status": "new"})
    store.create_approval("seed", "send_email", "draft-x", "seeded")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "agentkit", "--root", str(cfg.root), "mc", "--port", str(port)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("mission control did not start:\n" + (proc.stdout.read() if proc.stdout else ""))
    yield url, cfg
    proc.kill()


def test_dashboard_clean_across_all_tabs(mc):
    url, cfg = mc
    from playwright.sync_api import sync_playwright
    console_errors, bad = [], []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"chromium not installed for playwright: {e}")
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.on("dialog", lambda d: d.accept(""))
        page.goto(url)
        page.wait_for_selector("text=Probe Agent")
        for t in ["Overview", "Leads", "Tasks", "Runs", "Approvals", "Activity", "Brain", "Schedule", "Doctor", "Report", "Chat"]:
            page.click(f"nav >> text={t}")
            page.wait_for_timeout(300)
            if t == "Leads":
                page.wait_for_selector("td:has-text('Acme')")
            if t == "Tasks":
                page.wait_for_selector("text=item one")
                page.wait_for_selector("td:has-text('draft_outreach')")
            if t == "Approvals":
                page.wait_for_selector("text=seeded")
            if t == "Activity":
                page.wait_for_selector("text=ledger VERIFIED")
            if t == "Brain":
                page.wait_for_selector("textarea#brainText")
                page.wait_for_selector("text=probing")
            if t == "Doctor":
                page.wait_for_selector("text=core files SOUL/AGENTS/USER/MEMORY present", timeout=60000)
        page.click("nav >> text=Approvals")
        page.click("button:has-text('Deny')")
        page.wait_for_selector("text=denied")
        page.click("nav >> text=Brain")
        page.wait_for_selector("textarea#brainText")
        page.fill("textarea#brainText", "# SOUL.md\n\nedited from e2e\n")
        page.click("button:has-text('Save SOUL.md')")
        page.wait_for_timeout(500)
        assert "edited from e2e" in cfg.core_files["SOUL"].read_text(encoding="utf-8")
        page.select_option("#taskSel", "probe")
        page.click("#runBtn")
        page.wait_for_selector("#runBtn:not(.hidden)", timeout=60000)
        page.click("nav >> text=Runs")
        page.wait_for_selector("text=halted")  # backend=none halts with a clear reason, and the report still exists
        page.click("nav >> text=Report")
        page.wait_for_selector("text=run report")
        page.click("nav >> text=Activity")
        page.wait_for_selector("text=run_finished")
        browser.close()
    assert console_errors == [], console_errors
    assert bad == [], bad
