"""Produce the 60-second demo video: neural narration (edge-tts) + recorded browser walkthrough (Playwright)
+ captions + mux (ffmpeg) -> agent-foundry-demo.mp4. Real dashboards, real data, nothing staged."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "demo"
OUT.mkdir(parents=True, exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VOICE = "en-US-GuyNeural"
FOUNDRY, SELLER = "http://127.0.0.1:8110", "http://127.0.0.1:8111"
REPO = "https://github.com/UncNeph/agent-foundry"

SEGMENTS = [
    {"caption": "WHAT I BUILT — Agent Foundry: an agent that builds agents",
     "text": "Agent Foundry: an agent whose only job is building other agents, each on one runtime: core files, skills, tasks, "
             "a tool allowlist, budget caps, approvals, a hash-chained ledger, and its own Mission Control."},
    {"caption": "HOW IT WORKS — commission → spec → generate → verify (5 gates) → package → catalogue → approvals",
     "text": "Drop in a commission. The Foundry validates the spec, generates the package, and verifies it five ways: doctor, tests, "
             "a real smoke run, ledger, agent card. Pass, and it's zipped, catalogued with a price, and three approvals appear. "
             "First build: the Agent Seller, in forty-one seconds."},
    {"caption": "WHAT MAKES IT DIFFERENT — nothing acts alone; the product is real",
     "text": "Nothing acts alone: every external action is a proposal I approve, on a verified ledger. The Seller prospects on the live web, "
             "qualifies, prices from 2026 benchmarks, and drafts outreach that waits in the Outbox. When search found only vendor blogs, "
             "it refused to invent leads."},
    {"caption": "PROOF — health, evals PASS, fault injection 6/6, CI green, public repo",
     "text": "It also tests and repairs what it builds: health, evals, fault injection, six of six contained, including a live prompt injection. "
             "Both Mission Controls are live now. Twenty-four tests, CI green, public repo under UncNeph. Drop in a commission, press Run."},
]

CAPTION_JS = """(txt) => {
  let bar = document.getElementById('demo-caption');
  if (!bar) { bar = document.createElement('div'); bar.id = 'demo-caption';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;padding:14px 28px;background:rgba(8,12,18,.92);color:#e8edf2;' +
      'font:600 22px/1.3 ui-sans-serif,system-ui,Segoe UI,sans-serif;border-top:2px solid #7dd3fc;z-index:99999;letter-spacing:.2px';
    document.body.appendChild(bar); }
  bar.textContent = txt; }"""


async def narrate() -> list[float]:
    durations = []
    for i, seg in enumerate(SEGMENTS):
        mp3 = OUT / f"seg{i}.mp3"
        await edge_tts.Communicate(seg["text"], VOICE, rate="+18%").save(str(mp3))
        wav = OUT / f"seg{i}.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "44100", "-ac", "2", str(wav)], check=True)
        with wave.open(str(wav)) as w:
            durations.append(w.getnframes() / w.getframerate())
    return durations


def hold(page, seconds: float) -> None:
    page.wait_for_timeout(max(0, int(seconds * 1000)))


def record(durations: list[float]) -> tuple[Path, float]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(OUT / "raw"),
                                  record_video_size={"width": 1440, "height": 900})
        page = ctx.new_page()
        t_ctx = time.time()
        # ---- segment 1: Foundry overview
        page.goto(FOUNDRY)
        page.wait_for_selector("text=Agent Foundry")
        page.wait_for_timeout(1200)
        page.evaluate(CAPTION_JS, SEGMENTS[0]["caption"])
        audio_start = time.time() - t_ctx
        hold(page, durations[0])
        # ---- segment 2: Commissions -> Builds -> Catalog
        d = durations[1]
        page.evaluate(CAPTION_JS, SEGMENTS[1]["caption"])
        page.click("nav >> text=Commissions"); hold(page, d * 0.22)
        page.click("nav >> text=Builds"); hold(page, d * 0.30)
        page.click("nav >> text=Catalog"); hold(page, d * 0.48)
        # ---- segment 3: Approvals -> Activity -> Seller Leads -> Outbox
        d = durations[2]
        page.evaluate(CAPTION_JS, SEGMENTS[2]["caption"])
        page.click("nav >> text=Approvals"); hold(page, d * 0.20)
        page.click("nav >> text=Activity"); page.wait_for_selector("text=ledger VERIFIED"); hold(page, d * 0.14)
        page.goto(SELLER); page.wait_for_selector("text=Agent Seller")
        page.click("nav >> text=Leads"); page.wait_for_selector("td:has-text('Mariana Tek')")
        page.evaluate(CAPTION_JS, SEGMENTS[2]["caption"]); hold(page, d * 0.34)
        page.click("nav >> text=Outbox"); page.wait_for_timeout(600); hold(page, d * 0.32)
        # ---- segment 4: Health -> GitHub
        d = durations[3]
        page.evaluate(CAPTION_JS, SEGMENTS[3]["caption"])
        page.click("nav >> text=Health"); page.wait_for_selector(".pill:has-text('HEALTH ')", timeout=120000)
        page.evaluate(CAPTION_JS, SEGMENTS[3]["caption"]); hold(page, d * 0.55)
        page.goto(REPO); page.wait_for_load_state("load")
        page.evaluate(CAPTION_JS, SEGMENTS[3]["caption"]); hold(page, d * 0.45 + 0.3)
        video = page.video
        ctx.close()
        path = Path(video.path())
        browser.close()
    return path, audio_start


def mux(video: Path, audio_start: float) -> Path:
    inputs, filt = [], []
    for i in range(len(SEGMENTS)):
        inputs += ["-i", str(OUT / f"seg{i}.wav")]
        filt.append(f"[{i + 1}:a]")  # input 0 is the video; narration segments are inputs 1..n
    n = len(SEGMENTS)
    concat = "".join(filt) + f"concat=n={n}:v=0:a=1[a0];[a0]adelay={int(audio_start * 1000)}|{int(audio_start * 1000)}[a]"
    out = OUT / "agent-foundry-demo.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(video), *inputs, "-filter_complex", concat, "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                    "-shortest", str(out)], check=True)
    return out


def main() -> None:
    durations = asyncio.run(narrate())
    print("narration segments (s):", [round(d, 1) for d in durations], "total", round(sum(durations), 1))
    video, audio_start = record(durations)
    print("recorded", video, "audio starts at", round(audio_start, 2))
    out = mux(video, audio_start)
    probe = subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True)
    dur = next((ln.strip() for ln in probe.stderr.splitlines() if "Duration" in ln), "")
    print("DONE", out, dur)
    (OUT / "demo_meta.json").write_text(json.dumps({"segments": [s["caption"] for s in SEGMENTS], "durations": durations, "audio_start": audio_start}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
