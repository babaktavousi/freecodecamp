"""Headless pipeline: read -> verify (+AI) -> correct -> equipment data -> simulate -> bottlenecks."""
from __future__ import annotations

import argparse
import json
import sys

from . import rates
from .ai import AIClient, AIConfig
from .analysis import analyse, report_dict, report_text
from .simulate import SimSettings
from .verify import CorrectionSession, run_verification
from .visio_reader import load_any, read_visio_live


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bpsim-cli", description="Batching plant verification + DES simulation")
    ap.add_argument("model", nargs="?", help=".vsdx / .json model (omit with --live to read the open Visio page)")
    ap.add_argument("--live", action="store_true", help="read the page open in Visio (Windows + pywin32)")
    ap.add_argument("--no-ai", action="store_true", help="skip the AI check (rules only)")
    ap.add_argument("--auto-fix", action="store_true", help="apply all suggested corrections without asking")
    ap.add_argument("--defaults", action="store_true", help="accept default equipment data for anything missing")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--wip", type=int, default=4)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--variability", type=float, default=0.05)
    ap.add_argument("--target", type=float, help="target production in m3/h")
    ap.add_argument("--out", help="write a JSON report here")
    ap.add_argument("--save-model", help="write the corrected model (JSON) here")
    a = ap.parse_args(argv)

    m = read_visio_live() if a.live else load_any(a.model)
    print(f"Read {len(m.equipment)} equipment items and {len(m.connections)} connections from '{m.name}'.")
    ai = None if a.no_ai else AIClient(AIConfig.load())

    rep = run_verification(m, ai)
    print("\n== MODULE: VERIFICATION ==")
    for i in rep.issues:
        print(f" [{i.severity:7}] {i.message}")
    if rep.ai and rep.ai.available:
        print(f" AI check: complete={rep.ai.complete}. {rep.ai.summary}")
    elif rep.ai:
        print(f" AI check unavailable: {rep.ai.error}")
    if not rep.complete:
        sess = CorrectionSession(m, rep.ai.steps if rep.ai else [])
        step = 0
        while True:
            plan = sess.plan()
            if not plan:
                break
            fx = plan[0]
            step += 1
            print(f"\n Step {step}/{step - 1 + len(plan)}: {fx.title}\n    why: {fx.why}")
            if a.auto_fix or (sys.stdin.isatty() and input("    apply? [Y/n] ").strip().lower() in ("", "y")):
                ok, msg = sess.apply(fx)
                print("    ->", msg)
            else:
                sess.skip(fx)
        print("\n Final verification after corrections ...")
        rep = run_verification(m, ai)
        print(" " + rep.status_text())
    else:
        print(" " + rep.status_text())
    if not rep.complete:
        print("The model is still incomplete - fix the remaining errors in Visio and re-run.")
        return 2
    if a.save_model:
        m.save(a.save_model)

    print("\n== MODULE: EQUIPMENT DATA ==")
    missing = rates.needs_data(m)
    if missing and not a.defaults and not all(e.params for e in missing):
        print(" Equipment data is not final. Use the GUI dialog, put 'params' in the JSON, or pass --defaults.")
        return 3
    rates.apply_defaults(m)
    for e in m.equipment.values():
        if e.params:
            print(f" {e.id:8} " + ", ".join(f"{k}={v:g}" for k, v in e.params.items()))

    print("\n== MODULE: SIMULATION & BOTTLENECKS ==")
    st = SimSettings(duration_h=a.hours, wip=a.wip, replications=a.reps, variability=a.variability)
    an = analyse(m, st, a.target)
    print(report_text(m, an))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report_dict(m, an), f, indent=2)
    return 0
