"""Rebuild samples/sample_report_PROVISIONAL.pdf - the full pipeline on the fictitious sample drawing, offline.

The adhesive data comes from a short verbatim excerpt of a PUBLIC European Technical Assessment (Hilti HIT-RE 500 V3,
ETA-16/0143) used as the 'attached TDS'; the report is deliberately marked PROVISIONAL because values are machine-read and
unconfirmed.  Usage: python samples/make_sample_report.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ANCHORCHECK_HOME", tempfile.mkdtemp())

from anchorcheck import pipeline                      # noqa: E402
from anchorcheck.config import Settings               # noqa: E402
from anchorcheck.knowledge.store import KnowledgeBase  # noqa: E402
from anchorcheck.report.builder import build_report   # noqa: E402
from tests.fixtures.eta_excerpt_pages import PAGES    # noqa: E402

st = Settings()
st.web.offline = True
kb = KnowledgeBase(Path(os.environ["ANCHORCHECK_HOME"]) / "kb")
kb.add_pages("ETA-16-0143_excerpt.pdf", PAGES)
drawing = ROOT / "samples" / "sample_structural_drawing.pdf"
s = pipeline.extract_drawings([(drawing.name, drawing.read_bytes())], st, kb=kb,
                              project=pipeline.ProjectInfo(name="Demo Warehouse Extension (fictitious)", reference="DEMO-001",
                                                           client="Demo Client Pty Ltd", revision="A"))
s.cases[0].adhesive_name = "HIT-RE 500 V3"      # so both groups have product data attached
pipeline.resolve_all_specs(s, st, kb, allow_web=False)
pipeline.run_analysis(s, kb)
out = build_report(s, ROOT / "samples" / "sample_report_PROVISIONAL.pdf")
print(s.verdict, "->", out)
