"""AnchorCheck - local web app (Streamlit).  Run:  python -m anchorcheck ui

Workflow: 1 Upload -> 2 Review extracted data -> 3 Standards -> 4 Adhesive specification -> 5 Analysis -> 6 Report.
AI reads and drafts; the engineer confirms every input; deterministic code calculates.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))     # make `anchorcheck` importable from any cwd

import pandas as pd
import streamlit as st

from anchorcheck import __version__, pipeline
from anchorcheck.config import Settings
from anchorcheck.design import DesignBasis
from anchorcheck.design.steel import ROD_GRADES, BAR_GRADES
from anchorcheck.knowledge.store import KnowledgeBase
from anchorcheck.llm.clients import LLMError, OllamaClient, make_client
from anchorcheck.models import BondEntry, Concrete, DesignCase, Layout, LoadCombo
from anchorcheck.products import library
from anchorcheck.products.compare import compare_spec
from anchorcheck.products.registry import PRODUCTS, find_product_by_name, norm
from anchorcheck.report.builder import build_report

st.set_page_config(page_title="AnchorCheck - anchor & dowel review", page_icon="🔩", layout="wide")

S = st.session_state
S.setdefault("settings", Settings.load())
S.setdefault("session", None)
S.setdefault("report", None)
S.setdefault("log", [])


def get_kb() -> KnowledgeBase:
    stg: Settings = S.settings
    emb = None
    if stg.embed_model and stg.llm.provider == "ollama":
        cl = OllamaClient(stg.llm)
        emb = lambda texts: cl.embed(texts, stg.embed_model)
    if "kb" not in S or S.get("kb_embed") != stg.embed_model:
        S.kb = KnowledgeBase(embed_fn=emb)
        S.kb_embed = stg.embed_model
    return S.kb


def badge(status: str) -> str:
    return {"PASS": "🟢 PASS", "FAIL": "🔴 FAIL", "INCOMPLETE": "🟠 INCOMPLETE", "WARN": "🟠 WARN", "NA": "⚪ n/a",
            "OK": "🟢 OK", "GAP": "🔴 GAP", "UNKNOWN": "🟠 UNKNOWN", "INFO": "⚪ info"}.get(status, status)


# ===================================================================== sidebar
def sidebar() -> None:
    stg: Settings = S.settings
    st.sidebar.title("🔩 AnchorCheck")
    st.sidebar.caption(f"v{__version__} · AS 5216 / AS 3600 anchor & dowel review")
    with st.sidebar.expander("🤖 AI model", expanded=False):
        prov = st.selectbox("Provider", ["none", "ollama", "anthropic", "openai"],
                            index=["none", "ollama", "anthropic", "openai"].index(stg.llm.provider), key="prov",
                            help="'none' = rule-based extraction only. Ollama runs locally on your PC.")
        stg.llm.provider = prov
        if prov == "ollama":
            stg.llm.host = st.text_input("Ollama host", stg.llm.host)
            stg.llm.model = st.text_input("Text model", stg.llm.model or "llama3.1:8b")
            stg.llm.vision_model = st.text_input("Vision model (reads drawn shapes)", stg.llm.vision_model or "qwen2.5vl:7b",
                                                 help="e.g. qwen2.5vl, llama3.2-vision, llava")
            stg.embed_model = st.text_input("Embedding model (optional, for attached documents)", stg.embed_model or "")
        elif prov == "anthropic":
            stg.llm.model = st.text_input("Model", stg.llm.model or "claude-sonnet-5-5")
            stg.llm.api_key = st.text_input("API key (or set ANTHROPIC_API_KEY)", stg.llm.api_key, type="password")
        elif prov == "openai":
            stg.llm.base_url = st.text_input("Base URL (OpenAI-compatible)", stg.llm.base_url)
            stg.llm.model = st.text_input("Model", stg.llm.model or "gpt-4o")
            stg.llm.api_key = st.text_input("API key", stg.llm.api_key, type="password")
        if st.button("Test connection"):
            ok, msg = make_client(stg.llm).available()
            (st.success if ok else st.error)(msg)
            if ok and prov == "ollama":
                try:
                    st.caption("Models: " + ", ".join(OllamaClient(stg.llm).list_models()))
                except LLMError as e:
                    st.warning(str(e))
    with st.sidebar.expander("🌐 Internet / web search", expanded=False):
        stg.web.offline = st.checkbox("Offline mode (never use the internet)", stg.web.offline)
        stg.web.engine = st.selectbox("Search engine", ["duckduckgo", "brave", "searxng"],
                                      index=["duckduckgo", "brave", "searxng"].index(stg.web.engine))
        if stg.web.engine == "brave":
            stg.web.brave_key = st.text_input("Brave API key (or BRAVE_API_KEY)", stg.web.brave_key, type="password")
        if stg.web.engine == "searxng":
            stg.web.searxng_url = st.text_input("SearXNG URL", stg.web.searxng_url)
    with st.sidebar.expander("📁 Project", expanded=True):
        stg.project_ref = st.text_input("Project reference", stg.project_ref, key="sb_ref")
        stg.engineer = st.text_input("Engineer", stg.engineer, key="sb_eng")
    if st.sidebar.button("💾 Save settings"):
        stg.save()
        st.sidebar.success("Saved (API keys are never written to disk).")
    st.sidebar.markdown("---")
    st.sidebar.caption("Software-assisted review. The responsible engineer must verify all inputs, product data and results.")


# ======================================================================== tab 1
def tab_upload() -> None:
    stg: Settings = S.settings
    kb = get_kb()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Structural drawings (PDF)")
        files = st.file_uploader("Drawings showing chemical anchors / dowels", type=["pdf"], accept_multiple_files=True, key="drw")
        use_vision = st.checkbox("Also read drawn shapes with the vision model", value=stg.llm.provider != "none",
                                 help="Recognises anchors/dowels from their drawn shape as well as the notes. Needs a vision-capable model.")
        fc = st.number_input("Assumed f'c (MPa) if the drawing does not state it", 20.0, 80.0, 32.0, 1.0)
        auto_spec = st.checkbox("Find each adhesive's specification automatically (attached documents → saved library → internet)",
                                value=True, help="The internet is only searched when no attached document covers the product.")
        name = st.text_input("Project name", S.get("proj_name", ""), key="up_name")
        client = st.text_input("Client", S.get("client", ""), key="up_client")
        if st.button("📖 Read drawings", type="primary", disabled=not files):
            S.proj_name, S.client = name, client
            logbox = st.status("Reading drawings…", expanded=True)
            try:
                proj = pipeline.ProjectInfo(name=name or Path(files[0].name).stem, reference=stg.project_ref, client=client,
                                            engineer=stg.engineer)
                S.session = pipeline.extract_drawings([(f.name, f.getvalue()) for f in files], stg, use_vision, kb,
                                                      progress=lambda m: logbox.write(m), project=proj, defaults={"fc": fc})
                S.report = None
                if auto_spec:
                    logbox.write("Finding adhesive specifications…")
                    pipeline.resolve_all_specs(S.session, stg, kb, allow_web=not stg.web.offline, progress=lambda m: logbox.write(m))
                    for cid, src in S.session.spec_source.items():
                        logbox.write(f"Specification for {cid}: {src}")
                logbox.update(label=f"Found {len(S.session.cases)} anchor/dowel group(s)", state="complete")
            except Exception as e:  # surface, never crash the app
                logbox.update(label="Failed", state="error")
                st.exception(e)
    with c2:
        st.subheader("Reference documents (optional)")
        st.caption("Attach the **AS standard** (e.g. AS 5216:2021, AS 3600) and/or the **TDS / ETA** of the adhesive. "
                   "They are indexed locally and used first; the internet is only searched when nothing is attached.")
        refs = st.file_uploader("AS standards, TDS, ETA, design guides", type=["pdf"], accept_multiple_files=True, key="refs")
        kind = st.selectbox("Document type", ["auto-detect", "standard", "tds", "eta", "guide", "other"])
        if st.button("➕ Add to knowledge base", disabled=not refs):
            for f in refs:
                try:
                    info = kb.add_pdf(f.getvalue(), f.name, None if kind == "auto-detect" else kind)
                    st.success(f"{info.name}: {info.kind}, {info.pages} pages {', '.join(info.codes)}")
                except Exception as e:
                    st.error(f"{f.name}: {e}")
        docs = kb.list_docs()
        if docs:
            st.dataframe(pd.DataFrame([{"document": d.name, "type": d.kind, "pages": d.pages, "standard codes": ", ".join(d.codes),
                                        "added": d.added} for d in docs]), hide_index=True)
            rm = st.selectbox("Remove a document", [""] + [d.name for d in docs])
            if rm and st.button("🗑 Remove"):
                kb.remove(rm)
                st.rerun()
        else:
            st.info("Nothing attached yet.")
    s = S.session
    if s is not None:
        st.divider()
        ex = s.extraction
        cols = st.columns(4)
        cols[0].metric("Pages", len(ex.pages))
        cols[1].metric("Groups found", len(s.cases))
        cols[2].metric("Products named", len({m.name for m in ex.adhesive_mentions}))
        cols[3].metric("Reading method", " + ".join(ex.methods))
        for w in ex.warnings:
            st.warning(w)


# ======================================================================== tab 2
def load_df(c: DesignCase) -> pd.DataFrame:
    return pd.DataFrame([{"name": l.name, "N* (kN, +tension)": l.N, "Vx (kN)": l.Vx, "Vy (kN)": l.Vy, "Mx (kNm)": l.Mx,
                          "My (kNm)": l.My, "sustained fraction": l.sustained_fraction, "basis": l.basis,
                          "limit state": l.limit_state} for l in c.loads] or
                        [{"name": "ULS", "N* (kN, +tension)": 0.0, "Vx (kN)": 0.0, "Vy (kN)": 0.0, "Mx (kNm)": 0.0, "My (kNm)": 0.0,
                          "sustained fraction": 0.0, "basis": "group", "limit state": "ULS"}])


def tab_review() -> None:
    s: pipeline.Session = S.session
    if s is None:
        st.info("Upload and read a drawing first (tab 1).")
        return
    st.caption("Everything below came from the drawing (or is a flagged assumption). **Check it against the drawing and correct anything wrong** - "
               "the calculations use exactly what is shown here.")
    if s.rfis:
        with st.expander(f"❓ Information missing from the drawings ({len(s.rfis)})", expanded=True):
            for r in s.rfis:
                st.markdown("- " + r)
    for w in s.extraction.warnings:
        st.warning(w)
    if st.button("➕ Add anchor / dowel group manually"):
        n = len(s.cases) + 1
        s.cases.append(DesignCase(id=f"M{n}", title="Manually added group", loads=[LoadCombo(name="ULS")]))
        st.rerun()
    for idx, c in enumerate(list(s.cases)):
        icon = "🔩" if c.kind == "anchor" else "〰️"
        with st.expander(f"{icon} {c.id} · {c.title}", expanded=idx == 0):
            if c.notes:
                for n in c.notes:
                    st.caption("• " + n)
            a, b, d = st.columns(3)
            c.kind = a.selectbox("Type", ["anchor", "rebar"], index=["anchor", "rebar"].index(c.kind), key=f"{c.id}_kind",
                                 format_func=lambda x: "Chemical anchor (threaded rod)" if x == "anchor" else "Post-installed rebar")
            c.fastener_d = b.number_input("Diameter (mm)", 6.0, 50.0, float(c.fastener_d), 1.0, key=f"{c.id}_d")
            grades = list(ROD_GRADES) if c.kind == "anchor" else list(BAR_GRADES)
            c.grade = d.selectbox("Steel grade", grades, index=grades.index(c.grade) if c.grade in grades else 0, key=f"{c.id}_g")
            a, b, d = st.columns(3)
            c.h_ef = a.number_input("Effective embedment h_ef (mm)", 30.0, 2000.0, float(c.h_ef), 5.0, key=f"{c.id}_h")
            c.concrete.fc = b.number_input("Concrete f'c (MPa)", 10.0, 100.0, float(c.concrete.fc), 1.0, key=f"{c.id}_fc")
            th = d.number_input("Member thickness (mm, 0 = unknown)", 0.0, 5000.0, float(c.concrete.thickness or 0), 10.0, key=f"{c.id}_th")
            c.concrete.thickness = th or None
            a, b, d = st.columns(3)
            c.concrete.cracked = a.checkbox("Cracked concrete (conservative)", c.concrete.cracked, key=f"{c.id}_cr")
            c.hole_condition = b.selectbox("Hole condition", ["dry_wet", "flooded"], index=["dry_wet", "flooded"].index(c.hole_condition), key=f"{c.id}_hc")
            c.drilling = d.selectbox("Drilling", ["hammer", "diamond", "compressed_air"],
                                     index=["hammer", "diamond", "compressed_air"].index(c.drilling if c.drilling in ("hammer", "diamond", "compressed_air") else "hammer"),
                                     key=f"{c.id}_dr")
            st.markdown("**Layout** (edge distance 0 = remote / not near an edge)")
            a, b, d, e = st.columns(4)
            c.layout.n_x = int(a.number_input("Anchors in x", 1, 20, int(c.layout.n_x), key=f"{c.id}_nx"))
            c.layout.n_y = int(b.number_input("Anchors in y", 1, 20, int(c.layout.n_y), key=f"{c.id}_ny"))
            c.layout.s_x = d.number_input("Spacing x (mm)", 0.0, 2000.0, float(c.layout.s_x), 5.0, key=f"{c.id}_sx")
            c.layout.s_y = e.number_input("Spacing y (mm)", 0.0, 2000.0, float(c.layout.s_y), 5.0, key=f"{c.id}_sy")
            a, b, d, e = st.columns(4)
            for col, attr, label in ((a, "c_xneg", "Edge -x (mm)"), (b, "c_xpos", "Edge +x (mm)"), (d, "c_yneg", "Edge -y (mm)"), (e, "c_ypos", "Edge +y (mm)")):
                v = col.number_input(label, 0.0, 5000.0, float(getattr(c.layout, attr) or 0), 5.0, key=f"{c.id}_{attr}")
                setattr(c.layout, attr, v or None)
            a, b, d = st.columns(3)
            c.adhesive_name = a.text_input("Adhesive product", c.adhesive_name, key=f"{c.id}_ad",
                                           help="Type the commercial name; recognised products are listed in tab 4.")
            c.fixture.thickness = b.number_input("Plate thickness (mm)", 0.0, 200.0, float(c.fixture.thickness), 1.0, key=f"{c.id}_pt")
            c.fixture.standoff = d.number_input("Stand-off / gap under plate (mm)", 0.0, 200.0, float(c.fixture.standoff), 1.0, key=f"{c.id}_so")
            a, b, d = st.columns(3)
            c.overhead = a.checkbox("Overhead installation", c.overhead, key=f"{c.id}_oh")
            c.seismic = b.checkbox("Seismic action applies (not designed here)", c.seismic, key=f"{c.id}_se")
            c.shear_to_all_anchors = d.checkbox("Standard clearance holes (shear shared by all anchors)", c.shear_to_all_anchors, key=f"{c.id}_sh")
            st.markdown("**Design actions (factored ULS)** - `group` = total on the whole group; `per_anchor` = per anchor/bar")
            ed = st.data_editor(load_df(c), num_rows="dynamic", key=f"{c.id}_loads",
                                column_config={"basis": st.column_config.SelectboxColumn(options=["group", "per_anchor"]),
                                               "limit state": st.column_config.SelectboxColumn(options=["ULS", "SLS", "unknown"])})
            loads = []
            for _, r in ed.iterrows():
                if pd.isna(r.get("name")):
                    continue
                loads.append(LoadCombo(name=str(r["name"]), N=float(r["N* (kN, +tension)"] or 0), Vx=float(r["Vx (kN)"] or 0),
                                       Vy=float(r["Vy (kN)"] or 0), Mx=float(r["Mx (kNm)"] or 0), My=float(r["My (kNm)"] or 0),
                                       sustained_fraction=float(r["sustained fraction"] or 0), basis=str(r["basis"] or "group"),
                                       limit_state=str(r["limit state"] or "ULS")))
            c.loads = loads
            if c.prov:
                with st.popover("Where did these values come from?"):
                    for k, p in c.prov.items():
                        st.caption(f"**{k}**: {p.source} {p.document} {'p.' + str(p.page) if p.page else ''} {p.quote[:80]} (confidence {p.confidence:.2f})")
            if st.button("🗑 Delete this group", key=f"{c.id}_del"):
                s.cases.remove(c)
                st.rerun()
    pipeline.refresh_standards(s, get_kb())


# ======================================================================== tab 3
def tab_standards() -> None:
    s: pipeline.Session = S.session
    if s is None or s.standards is None:
        st.info("Read a drawing first.")
        return
    d = s.standards
    st.success(f"**Primary standard: {d.primary}** - {d.summary}")
    st.dataframe(pd.DataFrame([{"Standard": r.label, "Title": r.title, "Role": r.role, "Applies": "yes" if r.applies else "NO",
                                "Why": " ".join(r.reasons), "Attached": ", ".join(r.attached) or "-"} for r in d.refs]),
                 hide_index=True)
    if d.cited_on_drawing:
        st.markdown("**Cited on the drawing:** " + ", ".join(d.cited_on_drawing))
    for i in d.issues:
        st.warning(i)
    st.caption("AS standards are copyrighted. Attach your licensed copy in tab 1 so relevant passages are retrieved and cited in the report.")
    kb = get_kb()
    q = st.text_input("Search the attached documents (e.g. 'pry-out failure', 'development length')")
    if q:
        for h in kb.search(q, 5, kinds=["standard", "guide"]):
            st.markdown(f"**{h.doc}** p.{h.page} · score {h.score}")
            st.caption(" ".join(h.text.split())[:420] + "…")


# ======================================================================== tab 4
def tab_adhesive() -> None:
    s: pipeline.Session = S.session
    stg: Settings = S.settings
    if s is None:
        st.info("Read a drawing first.")
        return
    kb = get_kb()
    names = list(dict.fromkeys([c.adhesive_name for c in s.cases if c.adhesive_name]))
    if not names:
        st.warning("No adhesive product is named for any group. Enter a product name in tab 2 (or the drawing must state one).")
    for m in s.extraction.adhesive_mentions:
        if m.product:
            st.caption(f"Recognised on drawing: **{m.product.manufacturer} {m.product.name}** ({m.product.chemistry or 'chemistry n/a'}) · confidence {m.confidence:.2f} · {m.product.website}")
        else:
            st.caption(f"Possible product on drawing: {m.name} (not in the built-in registry - the web lookup will search for it)")
    for name in names:
        st.subheader(name)
        cases = [c for c in s.cases if c.adhesive_name == name]
        spec = next((s.specs.get(c.id) for c in cases if s.specs.get(c.id)), None)
        src = next((s.spec_source.get(c.id) for c in cases), "none")
        a, b, c3 = st.columns([2, 2, 3])
        if a.button("🔎 Find specification", key=f"find_{name}", help="Attached documents → saved library → internet"):
            with st.status("Looking for the specification…", expanded=True) as stat:
                sp, source, look = pipeline.resolve_spec(name, cases[0].kind, stg, kb, make_client(stg.llm), not stg.web.offline,
                                                         None, progress=lambda m: stat.write(m))
                for c in cases:
                    s.specs[c.id], s.spec_source[c.id] = sp, source
                    if look:
                        s.lookups[c.id] = look
                stat.update(label=f"Specification source: {source}", state="complete")
            st.rerun()
        up = b.file_uploader("…or attach this product's TDS/ETA", type=["pdf"], key=f"tds_{name}")
        if up is not None and b.button("Use this document", key=f"use_{name}"):
            info = kb.add_pdf(up.getvalue(), up.name, "tds")
            sp, source, look = pipeline.resolve_spec(name, cases[0].kind, stg, kb, make_client(stg.llm), False, [info.name])
            for c in cases:
                s.specs[c.id], s.spec_source[c.id] = sp, source
            st.rerun()
        c3.caption(f"Current source: **{src}**")
        link = c3.text_input("…or paste a link to the TDS / ETA", key=f"url_{name}", placeholder="https://…/technical-data-sheet.pdf")
        if link and c3.button("Fetch from link", key=f"fetch_{name}"):
            from anchorcheck.products.lookup import lookup_from_url
            look = lookup_from_url(name, link.strip(), stg, make_client(stg.llm) if stg.llm.provider != "none" else None, cases[0].kind)
            if look.spec is not None:
                for c in cases:
                    s.specs[c.id], s.spec_source[c.id], s.lookups[c.id] = look.spec, "internet (user link)", look
                st.rerun()
            else:
                st.error("; ".join(look.notes) or "Could not read that link.")
        if spec is None:
            st.warning("No specification yet. Design checks that need bond data stay INCOMPLETE.")
            continue
        look = next((s.lookups.get(c.id) for c in cases if s.lookups.get(c.id)), None)
        if look and look.attempts:
            with st.expander("Documents consulted"):
                for att in look.attempts:
                    st.markdown(f"- {'✅' if att.ok else '❌'} {att.url} · {att.trust} · {att.note}")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Approvals", ", ".join(spec.eta[:2] + spec.eads[:2]) or "none found")
        k2.metric("Bond entries", len(spec.bond))
        k3.metric("γ_inst (hammer)", spec.gamma_inst_map.get("hammer", spec.gamma_inst) or "n/a")
        k4.metric("ψ0_sus", spec.psi_sus0 or "n/a")
        for n in (look.notes if look else []):
            st.caption("ℹ " + n)
        sel = [e for e in spec.bond if any(abs(e.d - c.fastener_d) < 0.51 for c in cases)]
        if sel:
            st.markdown("**Bond resistance for the sizes used** (N/mm², characteristic)")
            st.dataframe(pd.DataFrame([{"type": e.fastener, "d": e.d, "hole": e.condition, "drilling": e.drilling, "temp. range": e.temp_range,
                                        "years": e.service_life, "τRk,cr": e.tau_cr, "τRk,ucr": e.tau_ucr, "source": f"{e.prov.document[:40]} p.{e.prov.page}"}
                                       for e in sel]), hide_index=True)
        with st.expander("✏️ Enter / override values manually"):
            m1, m2, m3, m4 = st.columns(4)
            md = m1.number_input("d (mm)", 6.0, 50.0, float(cases[0].fastener_d), 1.0, key=f"md_{name}")
            mk = m2.selectbox("table", ["rod", "rebar"], key=f"mk_{name}")
            mc = m3.number_input("τRk,cr (MPa)", 0.0, 60.0, 0.0, 0.5, key=f"mc_{name}")
            mu = m4.number_input("τRk,ucr (MPa)", 0.0, 60.0, 0.0, 0.5, key=f"mu_{name}")
            g1, g2, g3 = st.columns(3)
            gi = g1.number_input("γ_inst (0 = unchanged)", 0.0, 3.0, 0.0, 0.1, key=f"gi_{name}")
            ps = g2.number_input("ψ0_sus (0 = unchanged)", 0.0, 1.0, 0.0, 0.01, key=f"ps_{name}")
            rq = g3.checkbox("Qualified for post-installed rebar (EAD 330087)", bool(spec.rebar_qualified), key=f"rq_{name}")
            if st.button("Apply manual values", key=f"apply_{name}"):
                if mc or mu:
                    from anchorcheck.models import Provenance
                    spec.bond = [e for e in spec.bond if not (e.d == md and e.fastener == mk and e.condition == "dry_wet" and e.drilling == "hammer" and e.service_life == 50)]
                    spec.bond.append(BondEntry(d=md, fastener=mk, tau_cr=mc or None, tau_ucr=mu or None,
                                               prov=Provenance(source="user", document="manual entry", confidence=1.0)))
                if gi:
                    spec.gamma_inst = gi
                if ps:
                    spec.psi_sus0 = ps
                spec.rebar_qualified = rq or spec.rebar_qualified
                st.rerun()
        ok = st.checkbox("✔ I have confirmed these values against the manufacturer's document", spec.verified, key=f"ver_{name}")
        spec.verified = ok
        if st.button("💾 Save to library", key=f"save_{name}"):
            st.success(f"Saved: {library.save_spec(spec)}")
        rows = compare_spec(cases[0], spec)
        st.markdown("**Design requirements vs product specification**")
        st.dataframe(pd.DataFrame([{"Requirement": r.topic, "Needed": r.required, "Product provides": r.provided, "Status": badge(r.status),
                                    "Note": r.note} for r in rows]), hide_index=True)


# ======================================================================== tab 5
def tab_analysis() -> None:
    s: pipeline.Session = S.session
    if s is None:
        st.info("Read a drawing first.")
        return
    kb = get_kb()
    if st.button("▶ Run structural checks", type="primary"):
        for c in s.cases:
            if c.id not in s.specs and c.adhesive_name:
                s.specs[c.id], s.spec_source[c.id] = None, "none"
        pipeline.run_analysis(s, kb)
        S.report = None
    if not s.results:
        st.info("Press **Run structural checks**.")
        return
    label, why = s.verdict
    (st.success if label == "ADEQUATE" else st.error if label.startswith("NOT ADEQ") else st.warning)(f"**{label}** - {why}")
    st.dataframe(pd.DataFrame([{"Case": c.id, "Description": c.title, "Adhesive": c.adhesive_name,
                                "Max utilisation": "-" if s.results[c.id].max_utilisation is None else f"{s.results[c.id].max_utilisation:.2f}",
                                "Governing": s.results[c.id].governing, "Status": badge(s.results[c.id].status)} for c in s.cases if c.id in s.results]),
                 hide_index=True)
    for c in s.cases:
        r = s.results.get(c.id)
        if not r:
            continue
        with st.expander(f"{c.id} · {c.title} · {badge(r.status)}"):
            for w in r.warnings:
                st.warning(w)
            for combo in r.combos:
                st.markdown(f"**Combination '{combo.combo.name}'** - max utilisation {combo.max_utilisation and round(combo.max_utilisation, 3)} ({combo.governing})")
                for ck in combo.checks:
                    if ck.utilisation is not None and ck.status in ("PASS", "FAIL"):
                        st.progress(min(ck.utilisation, 1.0), text=f"{ck.title}: {ck.utilisation:.2f} {badge(ck.status)}")
                    with st.popover(f"calc: {ck.title}"):
                        st.dataframe(pd.DataFrame([{"symbol": x.symbol, "description": x.description, "value": str(x.value), "unit": x.unit, "formula": x.formula}
                                                   for x in ck.steps]), hide_index=True)
                        if ck.R_d is not None:
                            st.markdown(f"R_k = {ck.R_k:.1f} kN · φ = {ck.phi:.3f} · R_d = {ck.R_d:.1f} kN · E_d = {ck.E_d:.1f} kN → **{ck.utilisation:.3f}** {badge(ck.status)}")
                        for n in ck.notes:
                            st.caption("• " + n)
            st.markdown("**Detailing**")
            st.dataframe(pd.DataFrame([{"check": d.title, "status": badge(d.status), "detail": "; ".join(f"{x.description}: {x.value}" for x in d.steps) or "; ".join(d.notes)}
                                       for d in r.detailing]), hide_index=True)
            for f in s.fixes.get(c.id, []):
                st.info("💡 " + f.description + f"  → max utilisation {f.new_max_util:.2f}")
    alts = [(c, s.alternatives[c.id]) for c in s.cases if c.id in s.alternatives]
    for c, al in alts:
        st.markdown(f"**Alternative adhesives for {c.id}** (same design, each product's data)")
        st.dataframe(pd.DataFrame([{"Product": a.name, "Result": badge(a.status), "Max util.": a.max_util and round(a.max_util, 3),
                                    "Governing": a.governing, "Data confirmed": a.verified} for a in al]), hide_index=True)


# ======================================================================== tab 6
def tab_report() -> None:
    s: pipeline.Session = S.session
    stg: Settings = S.settings
    if s is None or not s.results:
        st.info("Run the structural checks first (tab 5).")
        return
    c1, c2, c3 = st.columns(3)
    s.project.name = c1.text_input("Project name", s.project.name or "Untitled project", key="rep_name")
    s.project.reference = c2.text_input("Reference", s.project.reference, key="rep_ref")
    s.project.client = c3.text_input("Client", s.project.client, key="rep_client")
    c1, c2, c3 = st.columns(3)
    s.project.engineer = c1.text_input("Prepared by", s.project.engineer, key="rep_by")
    s.project.checker = c2.text_input("Checked by", s.project.checker, key="rep_chk")
    s.project.revision = c3.text_input("Revision", s.project.revision, key="rep_rev")
    ai = st.checkbox("Add an AI-drafted executive summary (written only from the computed results)", False,
                     disabled=stg.llm.provider == "none")
    st.subheader("Review comments")
    st.dataframe(pd.DataFrame([{"Severity": f.severity, "Case": f.case_id, "Category": f.category, "Comment": f.title, "Detail": f.detail[:240],
                                "Action": f.action} for f in s.findings]), hide_index=True)
    if st.button("📄 Generate PDF report", type="primary"):
        with st.spinner("Building report…"):
            if ai:
                s.ai_summary = pipeline.ai_executive_summary(s, make_client(stg.llm))
            tmp = Path(tempfile.mkdtemp()) / "report.pdf"
            build_report(s, tmp)
            S.report = tmp.read_bytes()
    if S.report:
        st.download_button("⬇ Download report (PDF)", S.report, file_name=f"anchor_review_{time.strftime('%Y%m%d')}.pdf",
                           mime="application/pdf", type="primary")
        st.success(f"Report ready ({len(S.report) // 1024} KB).")


def main() -> None:
    sidebar()
    st.title("Anchor & dowel structural review")
    st.caption("Chemical anchors · post-installed reinforcement · AS 5216 · AS 3600 — AI reads and drafts, deterministic code calculates, the engineer verifies.")
    t = st.tabs(["1 · Upload", "2 · Review data", "3 · Standards", "4 · Adhesive", "5 · Analysis", "6 · Report"])
    with t[0]:
        tab_upload()
    with t[1]:
        tab_review()
    with t[2]:
        tab_standards()
    with t[3]:
        tab_adhesive()
    with t[4]:
        tab_analysis()
    with t[5]:
        tab_report()


main()
