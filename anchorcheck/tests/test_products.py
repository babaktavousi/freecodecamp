import json

import pytest

from anchorcheck.config import Settings, WebSettings
from anchorcheck.models import AdhesiveSpec, BondEntry, Concrete, DesignCase, Layout, LoadCombo
from anchorcheck.products import compare, library, lookup, registry, tds, web
from tests.fixtures.eta_excerpt_pages import PAGES


@pytest.fixture(scope="module")
def parsed():
    return tds.parse_spec_text(PAGES, name="HIT-RE 500 V3", doc_name="ETA-16/0143 excerpt")


def entry(spec, fastener, d, cond="dry_wet", drill="hammer", tkey="40/24", life=50):
    for e in spec.bond:
        if (e.fastener, e.d, e.condition, e.drilling, e.temp_key, e.service_life) == (fastener, d, cond, drill, tkey, life):
            return e
    raise AssertionError(f"no entry {fastener} {d} {cond} {drill} {tkey} {life}")


# ------------------------------------------------------------------------------- TDS parser
def test_eta_number_with_letter_spacing(parsed):
    assert parsed.spec.eta == ["ETA-16/0143"]
    assert "EAD 330499" in parsed.spec.eads
    assert set(parsed.spec.seismic) == {"C1", "C2"}


def test_k_factors_and_splitting_rule(parsed):
    s = parsed.spec
    assert (s.k_cr, s.k_ucr) == (7.7, 11.0)
    assert s.c_cr_sp_rule_en is True


@pytest.mark.parametrize("d,ucr,cr", [(8, 19, 7.5), (12, 18, 9.5), (16, 17, 9.5), (30, 14, 8.5)])
def test_rod_bond_values_hammer_50y(parsed, d, ucr, cr):
    e = entry(parsed.spec, "rod", d)
    assert e.tau_ucr == ucr and e.tau_cr == cr


def test_decimal_commas_and_second_temperature_range(parsed):
    e = entry(parsed.spec, "rod", 12, tkey="70/43")
    assert e.tau_cr == 7.5 and e.tau_ucr == 14


def test_hole_conditions_and_drilling(parsed):
    assert entry(parsed.spec, "rod", 12, cond="flooded").tau_ucr == 15
    assert entry(parsed.spec, "rod", 12, drill="diamond").tau_ucr == 13
    assert entry(parsed.spec, "rod", 12, drill="diamond").tau_cr is None      # no cracked value for diamond cored


def test_100_year_values_kept_separate(parsed):
    assert entry(parsed.spec, "rod", 12, life=100).tau_cr == 7
    assert entry(parsed.spec, "rod", 12, life=50).tau_cr == 9.5


def test_sleeve_table_does_not_pollute_rod_data(parsed):
    # HIS sleeves share the M8..M20 header but must never overwrite threaded-rod values
    assert entry(parsed.spec, "rod", 12).tau_ucr == 18       # NOT 14 (the sleeve value)
    assert all(e.fastener in ("rod", "rebar") for e in parsed.spec.bond)


def test_rebar_table_separate_from_rod(parsed):
    e = entry(parsed.spec, "rebar", 16)
    assert (e.tau_ucr, e.tau_cr, e.n_rk_s) == (15, 10, 111)


def test_installation_parameters_attach_to_correct_sizes(parsed):
    e = entry(parsed.spec, "rod", 16)
    assert (e.d0, e.hef_min, e.hef_max, e.s_min, e.c_min) == (18, 80, 320, 75, 50)
    assert e.h_min is None      # printed as a formula ("hef + 2 d0"), and the T_max numbers must not leak into it


def test_installation_safety_factors(parsed):
    assert parsed.spec.gamma_inst_map == {"hammer": 1.0, "diamond": 1.4, "flooded": 1.4}
    assert parsed.spec.gamma_inst == 1.0


def test_psi_c_and_sustained(parsed):
    assert parsed.spec.psi_c[40.0] == 1.07 and parsed.spec.psi_c[20.0] == 1.0
    assert parsed.spec.psi_sus0_map == {"40/24": 0.88, "70/43": 0.70}
    assert parsed.spec.psi_sus0 == 0.70           # lowest = conservative


def test_provenance_records_source_line(parsed):
    e = entry(parsed.spec, "rod", 12)
    assert "Rk,ucr" in e.prov.quote and e.prov.page == 4


def test_number_in_text_guard():
    assert tds.number_in_text(9.5, "values 7,5 8 9,5 9,5")
    assert tds.number_in_text(18, "19 18 18 17")
    assert not tds.number_in_text(17.3, "19 18 18 17")
    assert not tds.number_in_text(8, "values 18 28")        # must not match inside other numbers


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def ask_json(self, *a, **k):
        return self.payload


def test_llm_fallback_rejects_hallucinated_numbers():
    pages = ["Characteristic bond resistance tau Rk cracked concrete d 16: 8,5 N/mm2 ; uncracked 14 N/mm2"]
    res = tds.parse_spec_text(pages, name="X")
    assert not res.spec.bond
    llm = FakeLLM({"entries": [
        {"d": 16, "tau_cr": 8.5, "tau_ucr": 14, "condition": "dry_wet", "quote": "8,5 ... 14"},
        {"d": 20, "tau_cr": 12.7, "tau_ucr": 21, "condition": "dry_wet", "quote": "made up"}]})
    notes = tds.llm_fill_missing(res, pages, llm, "x.pdf")
    assert len(res.spec.bond) == 1 and res.spec.bond[0].d == 16
    assert any("rejected" in n for n in notes)
    assert res.spec.bond[0].prov.confidence < 0.7


# --------------------------------------------------------------------------------- registry
@pytest.mark.parametrize("text,pid", [
    ("ADHESIVE ANCHORS: RAMSET CHEMSET REO 502 XTREM OR APPROVED EQUIVALENT", "ramset-reo502-xtrem"),
    ("N16 starter bars to be installed with Hilti HIT-RE 500 V3 epoxy", "hilti-hit-re-500-v3"),
    ("use Chemset™ 801 Xtrem™ XC2", "ramset-801-xtrem-xc2"),
    ("Sika AnchorFix-3001 resin", "sika-anchorfix-3001"),
    ("HIT HY 200-A chemical anchor M16", "hilti-hit-hy-200"),
])
def test_product_recognition(text, pid):
    ms = registry.match_products(text)
    assert ms and ms[0].product.id == pid and ms[0].confidence > 0.6


def test_longest_alias_wins_over_generic():
    ms = registry.match_products("ChemSet Reo 502 Xtrem")
    assert [m.product.id for m in ms] == ["ramset-reo502-xtrem"]


def test_unregistered_product_is_flagged_not_invented():
    ms = registry.match_products("Rawlplug R-KEM II 380 chemical anchor")
    assert ms and ms[0].product is None and ms[0].registered is False


def test_registry_has_no_design_values():
    # identity data only - numeric resistances must come from documents with provenance
    assert not hasattr(registry.PRODUCTS[0], "tau_rk")


# ------------------------------------------------------------------------- web helpers
DDG_HTML = '''
<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.hilti.com.au%2Fcontent%2FHIT-RE-500-V3-TDS.pdf&rut=abc">Hilti HIT-RE 500 V3 <b>Technical</b> data sheet</a>
<a class="result__snippet" href="x">Technical data sheet for <b>HIT-RE 500 V3</b></a></div>
<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.scribd.com%2Fdocument%2F123">scribd copy</a>
<a class="result__snippet" href="x">uploaded document</a></div>
'''


def test_ddg_parser_decodes_redirect_and_ranks_manufacturer_pdf_first():
    hits = web.parse_ddg_html(DDG_HTML)
    assert hits[0].url == "https://www.hilti.com.au/content/HIT-RE-500-V3-TDS.pdf"
    ranked = web.rank_hits(hits, "HIT-RE 500 V3", "hilti.com.au")
    assert ranked[0].trust == "manufacturer" and ranked[-1].trust == "low"


def test_offline_mode_blocks_network(tmp_path):
    c = web.WebClient(WebSettings(offline=True), cache_dir=tmp_path)
    with pytest.raises(web.WebError):
        c.search("x")
    with pytest.raises(web.WebError):
        c.fetch("https://example.com/a.pdf")


def test_fetch_rejects_non_http(tmp_path):
    c = web.WebClient(WebSettings(), cache_dir=tmp_path)
    with pytest.raises(web.WebError):
        c.fetch("file:///etc/passwd")


class FakeWeb:
    """Stands in for WebClient: one manufacturer PDF containing the ETA excerpt."""
    def search(self, q, n=8):
        return [web.SearchHit("Hilti HIT-RE 500 V3 ETA", "https://www.hilti.com.au/eta.pdf", "technical assessment"),
                web.SearchHit("junk", "https://www.scribd.com/x", "")]

    def fetch(self, url):
        if "scribd" in url:
            raise AssertionError("low-trust source must not be fetched")
        return web.FetchedDoc(url, "pdf", PAGES, [], "2026-10-06 10:00:00", False, "", "eta.pdf")


def test_web_lookup_end_to_end_with_fake_client():
    s = Settings()
    res = lookup.lookup_on_web("Hilti HIT-RE 500 V3", s, client=FakeWeb())
    assert res.spec is not None and res.source == "web"
    assert entry(res.spec, "rod", 16).tau_cr == 9.5
    assert res.spec.provenance["approvals"].document == "https://www.hilti.com.au/eta.pdf"
    assert "retrieved 2026-10-06" in res.spec.bond[0].prov.note
    assert all(a.trust != "low" for a in res.attempts)


def test_attached_documents_take_priority_and_report_missing():
    docs = [{"name": "TDS.pdf", "pages": PAGES}]
    res = lookup.spec_from_documents("HIT-RE 500 V3", docs, kind="anchor")
    assert res.source == "attached" and res.spec.bond
    assert "bond resistance tau_Rk table" not in res.missing


# -------------------------------------------------------------------------------- library
def test_library_roundtrip(tmp_path, parsed):
    p = library.save_spec(parsed.spec, tmp_path)
    back = library.load_specs(tmp_path)[parsed.spec.name]
    assert back.psi_c == parsed.spec.psi_c and len(back.bond) == len(parsed.spec.bond)
    assert back.bond[0].prov.quote == parsed.spec.bond[0].prov.quote
    assert back.gamma_inst_map == parsed.spec.gamma_inst_map


# -------------------------------------------------------------------------------- compare
def _case(**kw):
    c = DesignCase(fastener_d=16, grade="8.8", h_ef=125, layout=Layout(),
                   concrete=Concrete(fc=32, cracked=True), loads=[LoadCombo(N=30, Vx=5)])
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_compare_flags_gaps(parsed):
    rows = {r.topic: r for r in compare.compare_spec(_case(overhead=True, seismic=True, fire=True), parsed.spec)}
    assert rows["Pre-qualification (AS 5216)"].status == "OK"
    assert rows["Cracked concrete"].status == "OK"
    assert rows["Seismic (AS 5216 App. F)"].status == "OK"
    assert rows["Fire resistance"].status in ("GAP", "UNKNOWN")
    assert rows["Data status"].status == "UNKNOWN"          # machine-read, not yet confirmed
    assert rows["Embedment range"].status == "OK"


def test_compare_no_spec_is_a_gap():
    rows = compare.compare_spec(_case(), None)
    assert rows[0].status == "GAP"


def test_alternative_screening_sorts_and_marks_incomplete(parsed):
    empty = AdhesiveSpec(name="No data product")
    out = compare.screen_alternatives(_case(), {"No data product": empty, "HIT-RE 500 V3": parsed.spec})
    assert out[0].name == "HIT-RE 500 V3" and out[0].status in ("PASS", "FAIL")
    assert out[-1].status == "INCOMPLETE"


def test_engine_uses_parsed_eta_values_end_to_end(parsed):
    from anchorcheck.design import check_case
    res = check_case(_case(), parsed.spec)
    pull = next(c for c in res.combos[0].checks if c.key.startswith("N_pullout"))
    # cracked, hammer, dry/wet, lowest temp range (70/43): tau_cr = 7.5 N/mm2 for M16? -> M16 II = 7.5 per ETA
    assert any(s.symbol == "tau_Rk" and abs(float(s.value) - 7.5 * 1.0) < 0.6 for s in pull.steps)
    assert res.basis_table[0].value == pytest.approx(1 / 1.5, abs=1e-3)       # gamma_inst = 1.0 for hammer drilling


def test_lookup_from_user_link_with_fake_client():
    s = Settings()
    res = lookup.lookup_from_url("Hilti HIT-RE 500 V3", "https://www.hilti.com.au/eta.pdf", s, client=FakeWeb())
    assert res.spec is not None and entry(res.spec, "rod", 16).tau_ucr == 17
    assert res.attempts[0].trust == "user-supplied" and "retrieved" in res.spec.bond[0].prov.note


def test_lookup_from_link_respects_offline_mode():
    s = Settings()
    s.web.offline = True
    res = lookup.lookup_from_url("X", "https://example.com/a.pdf", s, client=FakeWeb())
    assert res.spec is None and any("Offline" in n for n in res.notes)
