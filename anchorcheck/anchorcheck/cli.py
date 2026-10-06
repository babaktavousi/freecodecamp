"""Command line:  python -m anchorcheck {ui | analyse | kb | verify}"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import Settings


def _settings(a) -> Settings:
    s = Settings.load()
    if getattr(a, "provider", None):
        s.llm.provider = a.provider
    if getattr(a, "model", None):
        s.llm.model = a.model
    if getattr(a, "vision_model", None):
        s.llm.vision_model = a.vision_model
    if getattr(a, "host", None):
        s.llm.host = a.host
    if getattr(a, "base_url", None):
        s.llm.base_url = a.base_url
    if getattr(a, "offline", False):
        s.web.offline = True
    if getattr(a, "embed_model", None):
        s.embed_model = a.embed_model
    return s


def _kb(settings: Settings):
    from .knowledge.store import KnowledgeBase
    from .llm.clients import OllamaClient
    emb = None
    if settings.embed_model and settings.llm.provider == "ollama":
        cl = OllamaClient(settings.llm)
        emb = lambda texts: cl.embed(texts, settings.embed_model)
    return KnowledgeBase(embed_fn=emb)


def cmd_ui(a) -> int:
    app = Path(__file__).parent / "app" / "streamlit_app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.headless", "false",
           "--browser.gatherUsageStats", "false"]
    if a.port:
        cmd += ["--server.port", str(a.port)]
    return subprocess.call(cmd)


def cmd_analyse(a) -> int:
    from . import pipeline
    from .llm.clients import make_client
    from .report.builder import build_report
    st = _settings(a)
    kb = _kb(st)
    for d in a.docs or []:
        info = kb.add_pdf(Path(d).read_bytes(), Path(d).name)
        print(f"attached: {info.name} [{info.kind}] {info.pages} pages {info.codes}")
    files = [(Path(f).name, Path(f).read_bytes()) for f in a.drawings]
    proj = pipeline.ProjectInfo(name=a.project or Path(a.drawings[0]).stem, reference=a.ref or "", client=a.client or "",
                                engineer=a.engineer or "", revision=a.rev)
    defaults = {"fc": a.fc} if a.fc else None
    s = pipeline.extract_drawings(files, st, use_vision=a.vision, kb=kb, project=proj, progress=print, defaults=defaults)
    print(f"cases identified: {len(s.cases)}")
    for c in s.cases:
        print(f"  {c.id}: {c.title} | adhesive: {c.adhesive_name or '-'}")
    pipeline.resolve_all_specs(s, st, kb, allow_web=not a.no_web, progress=print)
    for cid, src in s.spec_source.items():
        print(f"  spec {cid}: {src}")
    pipeline.run_analysis(s, kb)
    if a.ai_summary:
        s.ai_summary = pipeline.ai_executive_summary(s, make_client(st.llm))
    out = build_report(s, a.out)
    print(f"verdict: {s.verdict[0]} - {s.verdict[1]}")
    print(f"report: {out}")
    return 0 if s.verdict[0].startswith("ADEQUATE") else 2


def cmd_kb(a) -> int:
    st = _settings(a)
    kb = _kb(st)
    if a.action == "add":
        for f in a.files:
            info = kb.add_pdf(Path(f).read_bytes(), Path(f).name, a.kind)
            print(f"added {info.name} [{info.kind}] {info.pages} pages {info.codes}")
    elif a.action == "remove":
        for f in a.files:
            kb.remove(f)
            print("removed", f)
    else:
        for d in kb.list_docs():
            print(f"{d.name:50s} {d.kind:9s} {d.pages:4d} pp  {', '.join(d.codes)}")
    return 0


def cmd_verify(a) -> int:
    """Reproduce published manufacturer / AEFAC values with the engine (trust check)."""
    from .design import DesignBasis
    from .design import anchors as A
    from .design import rebar as R
    B, phi = DesignBasis(), 1 / 1.5
    rows = []
    for hef, pub in ((70, 24.3), (80, 29.7), (100, 41.5)):
        rows.append((f"Concrete cone, uncracked, f'c=32, hef={hef}", pub, A.n0_rk_c(32, hef, False, B) / 1000 * phi,
                     "Ramset SARB Table 2a"))
    for hef, pub in ((90, 70.8), (110, 95.7)):
        rows.append((f"Pry-out, hef={hef}", pub, 2 * A.n0_rk_c(32, hef, False, B) / 1000 * phi, "Ramset SARB Table 4e"))
    for d, hef, c1, pub in ((10, 70, 40, 4.3), (12, 90, 40, 4.7), (16, 110, 45, 6.2)):
        rows.append((f"Edge shear M{d}, c1={c1}", pub, A.v0_rk_c(32, d, hef, c1, False, B) / 1000 * phi, "Ramset SARB Table 4a-1"))
    for d, pub in ((12, 350), (16, 465), (20, 580), (24, 700), (32, 990), (40, 1345)):
        rows.append((f"Development length N{d}, f'c=32", pub, R.development_length(500, d, 32, 3 * d)[0], "Ramset SARB Table 2"))
    rows.append(("TN-08 Example 1 (N12, f'c=25) Lsy.t", 350, R.development_length(500, 12, 25, 36)[0], "AEFAC TN-08"))
    bad = 0
    print(f"{'Check':48s} {'published':>10s} {'AnchorCheck':>12s}  source")
    for name, pub, got, src in rows:
        tol = max(0.02 * pub, 0.15 if pub < 100 else 6)
        ok = abs(pub - got) <= tol
        bad += not ok
        print(f"{name:48s} {pub:10.1f} {got:12.1f}  {src} {'OK' if ok else '<-- MISMATCH'}")
    print("\nAll benchmarks reproduced." if not bad else f"\n{bad} benchmark(s) outside tolerance.")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="anchorcheck", description="Review chemical anchors / dowels on structural drawings (AS 5216, AS 3600).")
    ap.add_argument("--version", action="version", version=f"AnchorCheck {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def llm_args(p):
        p.add_argument("--provider", choices=["none", "ollama", "anthropic", "openai"])
        p.add_argument("--model")
        p.add_argument("--vision-model")
        p.add_argument("--host", help="Ollama host, default http://localhost:11434")
        p.add_argument("--base-url", help="OpenAI-compatible base URL")
        p.add_argument("--embed-model", help="Ollama embedding model for the knowledge base")
        p.add_argument("--offline", action="store_true", help="never touch the internet")

    u = sub.add_parser("ui", help="launch the local web app")
    u.add_argument("--port", type=int)
    u.set_defaults(fn=cmd_ui)

    an = sub.add_parser("analyse", help="analyse drawing PDF(s) and write the PDF report")
    an.add_argument("drawings", nargs="+")
    an.add_argument("--docs", nargs="*", help="reference PDFs to attach (AS standards, TDS, ETA)")
    an.add_argument("--out", default="anchor_review_report.pdf")
    an.add_argument("--project")
    an.add_argument("--ref")
    an.add_argument("--client")
    an.add_argument("--engineer")
    an.add_argument("--rev", default="A")
    an.add_argument("--fc", type=float, help="assumed f'c (MPa) when the drawing does not state it")
    an.add_argument("--vision", action="store_true", help="read drawn shapes with the vision model")
    an.add_argument("--no-web", action="store_true")
    an.add_argument("--ai-summary", action="store_true")
    llm_args(an)
    an.set_defaults(fn=cmd_analyse)

    kb = sub.add_parser("kb", help="manage attached reference documents")
    kb.add_argument("action", choices=["add", "list", "remove"])
    kb.add_argument("files", nargs="*")
    kb.add_argument("--kind", choices=["standard", "tds", "eta", "guide", "other"])
    llm_args(kb)
    kb.set_defaults(fn=cmd_kb)

    v = sub.add_parser("verify", help="reproduce published benchmark values with the engine")
    v.set_defaults(fn=cmd_verify)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
