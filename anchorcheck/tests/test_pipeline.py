"""Extraction, standards identification, knowledge base, vision (with a fake model), pipeline and report smoke tests."""
import json
import os
from pathlib import Path

import pytest

from anchorcheck import pipeline
from anchorcheck.config import Settings
from anchorcheck.extract import build, patterns, vision
from anchorcheck.extract.common import Candidate, DrawingExtraction, Evidence
from anchorcheck.knowledge.store import KnowledgeBase, chunk_text, detect_codes, guess_kind
from anchorcheck.llm.clients import LLMClient, LLMError
from anchorcheck.llm.jsonutil import extract_json
from anchorcheck.pdfio import PdfPage, read_pdf
from anchorcheck.standards.identify import cited_standards, detect_base_material, identify_standards
from tests.fixtures.eta_excerpt_pages import PAGES

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "sample_structural_drawing.pdf"


@pytest.fixture(scope="module", autouse=True)
def sample_pdf():
    if not SAMPLE.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("mk", ROOT / "samples" / "make_sample_drawing.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.make(SAMPLE)
    return SAMPLE


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANCHORCHECK_HOME", str(tmp_path))
    return tmp_path


def page(text, n=1):
    return PdfPage(n, text, 842, 1191)


# ---------------------------------------------------------------------------- patterns
def test_sample_drawing_notes_are_read_correctly():
    ex = patterns.extract_from_pages(read_pdf(SAMPLE), "sample")
    rod = next(c for c in ex.candidates if c.kind == "anchor")
    assert rod.get("size") == 20 and rod.get("grade") == "8.8" and rod.get("count") == 4
    assert rod.get("h_ef") == 200 and rod.get("fc") == 40 and rod.get("thickness") == 400
    assert rod.get("edge") == 150 and rod.get("s_anchor") == 200 and rod.get("d0") == 22
    assert "Reo 502" in rod.get("adhesive")
    assert {(l.kind, l.value, l.basis) for l in rod.loads} == {("tension", 60.0, "group"), ("shear", 20.0, "group")}
    bar = next(c for c in ex.candidates if c.kind == "rebar")
    assert bar.get("size") == 16 and bar.get("spacing") == 300 and bar.get("h_ef") == 400 and bar.get("fc") == 32
    assert bar.get("drilling") == "hammer" and "HIT-RE 500" in bar.get("adhesive")
    assert [(l.kind, l.value, l.basis) for l in bar.loads] == [("tension", 45.0, "per_metre")]


def test_embed_text_is_not_mistaken_for_edge_distance():
    """Regression: 'EMBED 400' must not yield an edge distance of 400 (via the letters 'ED')."""
    ex = patterns.extract_from_pages([page("N16 @ 300 CTS STARTER BARS 400 EMBED\n600 THK slab EPOXY")], "x")
    assert ex.candidates and ex.candidates[0].get("edge") is None


def test_concrete_grade_N32_is_not_a_bar():
    ex = patterns.extract_from_pages([page("CONCRETE N32 (f'c = 32 MPa) slab\nN16 @ 200 CTS dowels epoxy grouted 300 EMBED")], "x")
    sizes = [c.get("size") for c in ex.candidates if c.kind == "rebar"]
    assert sizes == [16]
    assert ex.candidates[0].get("fc") == 32


def test_scanned_pdf_warns_and_extracts_nothing():
    ex = patterns.extract_from_pages([page("")], "scan")
    assert not ex.candidates and any("No embedded text" in w for w in ex.warnings)


def test_missing_notes_checklist():
    ex = patterns.extract_from_pages([page("M16 chemical anchors 125 EMBED")], "x")
    joined = " ".join(ex.missing_notes)
    assert "AS 5216" in joined and "proof" in joined and "scanning" in joined


def test_sls_loads_are_labelled():
    ex = patterns.extract_from_pages([page("M16 GR 8.8 chemical anchor 125 EMBED\nSERVICE LOAD TENSION 20 kN per anchor")], "x")
    l = ex.candidates[0].loads[0]
    assert l.limit_state == "SLS" and l.basis == "per_anchor"


# ----------------------------------------------------------------------------- build
def test_cases_built_with_rfis_for_missing_information():
    ex = patterns.extract_from_pages([page("M16 chemical anchors 125 EMBED. TENSION 20 kN")], "x")
    cases, rfis = build.build_cases(ex)
    c = cases[0]
    assert c.grade == "4.6"                                    # conservative default, flagged
    assert any("steel grade" in r for r in rfis) and any("concrete strength" in r for r in rfis)
    assert any("edge distance" in r for r in rfis) and any("thickness" in r for r in rfis)
    assert c.concrete.cracked is True and c.concrete.stated_on_drawing is False


def test_per_metre_load_converted_using_bar_spacing():
    ex = patterns.extract_from_pages(read_pdf(SAMPLE), "sample")
    cases, _ = build.build_cases(ex)
    bar = next(c for c in cases if c.kind == "rebar")
    assert bar.loads[0].N == pytest.approx(45.0 * 0.300) and bar.loads[0].basis == "per_anchor"


def test_provenance_names_the_drawing_file():
    ex = patterns.extract_from_pages(read_pdf(SAMPLE), "sample")
    for c in ex.candidates:
        c.source = "sample.pdf"
    cases, _ = build.build_cases(ex)
    assert cases[0].prov["h_ef"].document == "sample.pdf" and cases[0].prov["h_ef"].page == 1
    assert cases[0].prov["h_ef"].quote


# ---------------------------------------------------------------------- vision (fake)
class FakeVLM(LLMClient):
    name = "fake"
    supports_vision = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def available(self):
        return True, "fake"

    def chat(self, system, prompt, images=None, json_mode=False, model=None):
        self.calls += 1
        assert images, "vision call must carry an image"
        return "Here you go:\n```json\n" + json.dumps(self.payload) + "\n```"


def test_vision_reads_shape_and_demotes_values_not_in_text():
    payload = {"details": [{"detail_ref": "Section A-A", "shape": "threaded_rod_with_base_plate",
                            "fastener_type": "chemical_anchor_rod", "size": "M20", "grade": "8.8", "embedment_mm": 200,
                            "concrete_mpa": 55, "adhesive_product": "Ramset ChemSet Reo 502 Xtrem",
                            "loads": [{"kind": "tension", "value": 60, "unit": "kN", "basis": "group", "limit_state": "ULS"}],
                            "confidence": 0.8, "evidence": "rods drawn through plate; label 200 EMBED"}]}
    pages = read_pdf(SAMPLE)
    ex = vision.extract_with_vision(str(SAMPLE), pages[:1], FakeVLM(payload))
    c = ex.candidates[0]
    assert c.kind == "anchor" and c.shape == "threaded_rod_with_base_plate" and c.get("size") == 20
    assert c.fields["h_ef"].confidence > 0.5                     # 200 appears in the embedded text
    assert c.fields["fc"].confidence <= 0.35                     # 55 MPa does NOT appear -> visual-only, demoted
    assert any("does not appear in the embedded text" in n for n in c.notes)


def test_vision_ignores_cast_in_and_mechanical():
    payload = {"details": [{"fastener_type": "mechanical", "size": "M12"}, {"fastener_type": "cast_in", "size": "M24"}]}
    ex = vision.extract_with_vision(str(SAMPLE), read_pdf(SAMPLE)[:1], FakeVLM(payload))
    assert ex.candidates == []


def test_merge_flags_conflicts_between_notes_and_image():
    prim = DrawingExtraction(candidates=[Candidate("anchor", 1, "M20", {"size": Evidence(20, 1, "M20"), "h_ef": Evidence(200.0, 1, "200 EMBED")})])
    sec = DrawingExtraction(candidates=[Candidate("anchor", 1, "M20v", {"size": Evidence(20, 1, "", 0.6, "vision"),
                                                                         "h_ef": Evidence(250.0, 1, "", 0.6, "vision")})])
    out = build.merge_extractions(prim, sec)
    assert out.candidates[0].get("h_ef") == 200.0                # text wins
    assert any("differs" in w and "250" in w for w in out.warnings)


def test_llm_error_surfaces_as_warning_not_crash():
    class Broken(FakeVLM):
        def chat(self, *a, **k):
            raise LLMError("model not found")
    ex = vision.extract_with_vision(str(SAMPLE), read_pdf(SAMPLE)[:1], Broken({}))
    assert ex.candidates == [] and any("model not found" in w for w in ex.warnings)


def test_json_extraction_from_chatty_output():
    assert extract_json("Sure!\n```json\n{\"a\": [1,2,],}\n```\nHope that helps") == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


# ------------------------------------------------------------------------ standards
def test_as5216_identified_for_chemical_anchors_in_concrete():
    d = identify_standards("M16 chemical anchors in 400 THK concrete slab. TENSION 20 kN", ["anchor"])
    assert d.primary == "AS 5216:2021"
    codes = {r.code for r in d.refs if r.applies}
    assert {"AS 5216", "AS 3600", "AS/NZS 1170.0"} <= codes


def test_rebar_dowels_cite_appendix_d_and_as3600():
    d = identify_standards("N16 post-installed starter bars into slab", ["rebar"])
    r = next(x for x in d.refs if x.code == "AS 5216")
    assert any("Appendix D" in s for s in r.sections) and any("reinforc" in w.lower() for w in r.reasons)
    assert any(x.code == "AS/NZS 4671" and x.applies for x in d.refs)


def test_masonry_is_not_covered_by_as5216():
    d = identify_standards("Chemical anchors into brick masonry wall", ["anchor"])
    assert d.base_material == "masonry" and d.primary == "AS 3700"
    assert not next(r for r in d.refs if r.code == "AS 5216").applies


def test_superseded_references_flagged():
    d = identify_standards("Anchors to SA TS 101 and AS 3600-2009. Concrete slab. AS 1250 steel.", ["anchor"])
    msgs = " ".join(d.issues)
    assert "SA TS 101" in msgs and "AS 3600:2009" in msgs and "AS 1250" in msgs


def test_missing_as5216_citation_is_recommended():
    d = identify_standards("M16 chemical anchors concrete slab", ["anchor"])
    assert any("does not cite AS 5216" in i for i in d.issues)


def test_seismic_flag_adds_as1170_4():
    d = identify_standards("Seismic restraint anchors concrete", ["anchor"])
    assert any(r.code == "AS 1170.4" and r.applies for r in d.refs)


def test_citation_parser():
    assert cited_standards("to AS 5216:2021 and AS/NZS 1170.0:2002, AS 3600") == ["AS 5216:2021", "AS 1170.0:2002", "AS 3600", "AS/NZS 1170.0:2002"]


# --------------------------------------------------------------------- knowledge base
def test_kb_search_ranks_relevant_passage_and_cites_page(home):
    kb = KnowledgeBase(home / "kb")
    kb.add_pages("AS5216-licensed.pdf", [
        "Standards Australia AS 5216:2021 Design of post-installed and cast-in fastenings in concrete. Scope and general.",
        "Concrete pry-out failure of anchors loaded in shear. The characteristic resistance V_Rk,cp = k8 N_Rk,c.",
        "Steel failure in tension: N_Rk,s = As fuk.",
    ])
    hits = kb.search("pry-out failure shear", k=2)
    assert hits and hits[0].page == 2 and "pry-out" in hits[0].text.lower()
    assert kb.docs["AS5216-licensed.pdf"].kind == "standard" and "AS 5216:2021" in kb.docs["AS5216-licensed.pdf"].codes


def test_kb_persists_and_removes(home):
    kb = KnowledgeBase(home / "kb")
    kb.add_pages("tds.pdf", PAGES)
    kb2 = KnowledgeBase(home / "kb")
    assert "tds.pdf" in kb2.docs and kb2.search("bond resistance", 1)
    kb2.remove("tds.pdf")
    assert not KnowledgeBase(home / "kb").docs


def test_kb_kind_detection():
    assert guess_kind("European Technical\nAssessment ETA-16/0143") == "eta"
    assert guess_kind("TECHNICAL DATA SHEET ChemSet") == "tds"
    assert detect_codes("AS 3600:2018 Concrete structures. AS 3600") == ["AS 3600:2018"] or detect_codes("AS 3600:2018")[0].startswith("AS 3600")


def test_kb_finds_product_documents_by_alias(home):
    kb = KnowledgeBase(home / "kb")
    kb.add_pages("eta.pdf", PAGES)
    assert kb.docs_mentioning(["HIT-RE 500 V3", "hit re 500 v3"]) == ["eta.pdf"]
    assert kb.docs_mentioning(["Sika AnchorFix-3001"]) == []


def test_hybrid_search_with_fake_embeddings(home):
    vocab = ["pry", "steel", "cone"]
    emb = lambda texts: [[float(t.lower().count(w)) for w in vocab] for t in texts]
    kb = KnowledgeBase(home / "kb", embed_fn=emb)
    kb.add_pages("g.pdf", ["steel failure tension", "pry out failure shear pry", "cone breakout"])
    assert kb.search("pry", 1)[0].page == 2


# ------------------------------------------------------------------------ pipeline
def test_pipeline_attached_eta_flows_through_to_results(home):
    st = Settings()
    st.web.offline = True
    kb = KnowledgeBase(home / "kb")
    kb.add_pages("ETA-16-0143_excerpt.pdf", PAGES)
    s = pipeline.extract_drawings([("sample.pdf", SAMPLE.read_bytes())], st, kb=kb)
    s.cases[0].adhesive_name = "HIT-RE 500 V3"
    pipeline.resolve_all_specs(s, st, kb, allow_web=False)
    assert s.spec_source["A1"] == "attached documents"
    pipeline.run_analysis(s, kb)
    a1 = s.results["A1"]
    assert a1.status == "PASS" and 0.3 < a1.max_utilisation < 1.0
    assert s.results["R1"].status == "INCOMPLETE"                # ETA is EAD 330499 only: rebar qualification not shown
    assert s.verdict[0] == "INCOMPLETE"
    assert s.standards.primary == "AS 5216:2021"


def test_unknown_product_is_never_given_invented_values(home):
    st = Settings()
    st.web.offline = True
    s = pipeline.extract_drawings([("sample.pdf", SAMPLE.read_bytes())], st, kb=KnowledgeBase(home / "kb"))
    pipeline.resolve_all_specs(s, st, KnowledgeBase(home / "kb"), allow_web=False)
    assert all(v is None for v in s.specs.values())
    pipeline.run_analysis(s)
    assert s.results["A1"].status in ("FAIL", "INCOMPLETE")
    assert not s.verdict[0] == "ADEQUATE"


def test_provisional_failure_is_labelled_as_such(home):
    st = Settings()
    st.web.offline = True
    s = pipeline.extract_drawings([("sample.pdf", SAMPLE.read_bytes())], st, kb=KnowledgeBase(home / "kb"))
    pipeline.resolve_all_specs(s, st, KnowledgeBase(home / "kb"), allow_web=False)
    s.cases = [c for c in s.cases if c.id == "A1"]
    s.cases[0].loads[0].N = 400.0                               # overload
    pipeline.run_analysis(s)
    assert s.verdict[0] == "NOT ADEQUATE - PROVISIONAL"          # product data missing -> default factors


def _failing_case(home, N, Vx):
    from anchorcheck.design import check_case
    st = Settings()
    st.web.offline = True
    kb = KnowledgeBase(home / "kb")
    kb.add_pages("eta.pdf", PAGES)
    s = pipeline.extract_drawings([("sample.pdf", SAMPLE.read_bytes())], st, kb=kb)
    c = s.cases[0]
    c.adhesive_name = "HIT-RE 500 V3"
    pipeline.resolve_all_specs(s, st, kb, allow_web=False)
    c.loads[0].N, c.loads[0].Vx = N, Vx
    return c, s.specs[c.id], check_case


def test_fix_suggestion_single_change_passes(home):
    from anchorcheck.review import suggest_fixes
    c, spec, check_case = _failing_case(home, 95.0, 20.0)
    assert check_case(c, spec).status == "FAIL"
    fixes = suggest_fixes(c, spec)
    assert fixes and all(f.passes and f.new_max_util <= 1.0 for f in fixes)
    # the suggestion must really work when applied
    import copy
    c2 = copy.deepcopy(c)
    if "h_ef" in fixes[0].changed:
        c2.h_ef = fixes[0].changed["h_ef"]
        assert check_case(c2, spec).status == "PASS"


def test_fix_suggestions_when_no_single_change_works(home):
    from anchorcheck.review import suggest_fixes
    c, spec, check_case = _failing_case(home, 150.0, 20.0)
    assert check_case(c, spec).status == "FAIL"
    fixes = suggest_fixes(c, spec)
    assert fixes                                         # something useful is always reported
    for f in fixes:
        assert f.passes == (f.new_max_util <= 1.0)       # honest labelling: never claims a pass that is not one
    base_u = check_case(c, spec).max_utilisation
    assert all(f.new_max_util < base_u for f in fixes)   # every suggestion actually improves the design


def test_report_pdf_is_generated_with_expected_content(home, tmp_path):
    from anchorcheck.report.builder import build_report
    st = Settings()
    st.web.offline = True
    kb = KnowledgeBase(home / "kb")
    kb.add_pages("ETA-16-0143_excerpt.pdf", PAGES)
    s = pipeline.extract_drawings([("sample.pdf", SAMPLE.read_bytes())], st, kb=kb,
                                  project=pipeline.ProjectInfo(name="Test Project", reference="TP-1"))
    s.cases[0].adhesive_name = "HIT-RE 500 V3"
    pipeline.resolve_all_specs(s, st, kb, allow_web=False)
    pipeline.run_analysis(s, kb)
    out = build_report(s, tmp_path / "r.pdf")
    pages = read_pdf(out)
    text = "\n".join(p.text for p in pages)
    assert len(pages) >= 10 and out.stat().st_size > 50_000
    for needle in ("Executive summary", "Applicable Australian Standards", "AS 5216:2021", "Test Project", "ETA-16/0143",
                   "Concrete cone failure", "φ", "τ", "Review comments", "Appendix A", "DRAFT"):
        assert needle in text, needle


def test_report_for_empty_project_does_not_crash(home, tmp_path):
    from anchorcheck.report.builder import build_report
    s = pipeline.Session()
    s.verdict = ("NO DESIGN CASES", "none")
    out = build_report(s, tmp_path / "empty.pdf")
    assert out.exists() and len(read_pdf(out)) >= 3


def test_cli_verify_passes():
    from anchorcheck.cli import main
    assert main(["verify"]) == 0


def test_cli_analyse_end_to_end(home, tmp_path, capsys):
    from anchorcheck.cli import main
    out = tmp_path / "cli.pdf"
    eta = tmp_path / "eta.pdf"
    # build a small PDF containing the ETA excerpt so the CLI can attach it
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(eta), pagesize=A4)
    for p in PAGES:
        y = 800
        for line in p.split("\n")[:60]:
            c.drawString(30, y, line[:110])
            y -= 12
        c.showPage()
    c.save()
    rc = main(["analyse", str(SAMPLE), "--docs", str(eta), "--out", str(out), "--no-web", "--offline", "--project", "CLI test"])
    assert rc in (0, 2) and out.exists()
    assert "report:" in capsys.readouterr().out
