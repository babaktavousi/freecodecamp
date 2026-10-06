"""Parse a manufacturer TDS / European Technical Assessment (ETA) into an AdhesiveSpec.

Two layers:
  1. deterministic regex/table parser (tested on a real ETA layout: letter-spaced ETA numbers,
     decimal commas, lost tau glyph, hole-type sections, 50/100-year blocks);
  2. optional LLM fallback whose numeric answers are only accepted if the number appears
     verbatim in the source text (guards against hallucinated values).

Every value carries a Provenance (document, page, the literal source line).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import AdhesiveSpec, BondEntry, Provenance

NUM = r"\d+(?:[.,]\d+)?"
UNIT_BRACKET = re.compile(r"\[[^\]]+\]")
DIA_LABEL = re.compile(r"(rebar|bar|rod|anchor|threaded|stud)[^\[]*diameter|diameter[^\[]*(rebar|bar|rod)", re.I)
HEADER_RE = re.compile(r"((?:[MØ⌀∅]?\s?\d{1,2}\s+){2,}[MØ⌀∅]?\s?\d{1,2})\s*$")
ROW_RE = re.compile(r"^(.*?)((?:(?:-|–|—|" + NUM + r")\s+)+(?:-|–|—|" + NUM + r"))\s*$")

INSTALL_ROWS = [
    ("d0", re.compile(r"(nominal )?diameter of (the )?drill bit|drill(ed)? hole diameter|\bd\s*0\s*\[mm\]", re.I)),
    ("hef_min", re.compile(r"h\s*ef\s*,?\s*min|minimum (effective )?embedment", re.I)),
    ("hef_max", re.compile(r"h\s*ef\s*,?\s*max|maximum (effective )?embedment", re.I)),
    ("s_min", re.compile(r"minimum spacing|\bs\s*min\s*\[mm\]", re.I)),
    ("c_min", re.compile(r"minimum edge distance|\bc\s*min\s*\[mm\]", re.I)),
    ("h_min", re.compile(r"minimum thickness of (the )?concrete member|\bh\s*min\s*\[mm\]", re.I)),
    ("n_rk_s", re.compile(r"N\s*Rk\s*,\s*s\s*\[kN\]", re.I)),
    ("v_rk_s", re.compile(r"V\s*0?\s*Rk\s*,\s*s\s*\[kN\]", re.I)),
    ("t_max", re.compile(r"maximum torque|\bT\s*max\s*\[N\s*m\]", re.I)),
    ("d_f", re.compile(r"clearance hole|\bd\s*f\s*\[mm\]", re.I)),
]
INSTALL_BOND_ATTRS = {"d0", "hef_min", "hef_max", "s_min", "c_min", "h_min", "n_rk_s", "v_rk_s"}


def fnum(s: str) -> float:
    return float(s.replace(",", "."))


def _size(tok: str) -> float:
    return float(re.sub(r"[^\d.]", "", tok))


@dataclass
class ParseResult:
    spec: AdhesiveSpec
    notes: List[str] = field(default_factory=list)
    found: Dict[str, bool] = field(default_factory=dict)
    n_bond_rows: int = 0
    confidence: float = 0.0

    def missing_for_design(self, kind: str = "anchor") -> List[str]:
        out = []
        s = self.spec
        if not s.bond:
            out.append("bond resistance tau_Rk table")
        if s.gamma_inst is None and not s.gamma_inst_map:
            out.append("installation safety factor gamma_inst")
        if s.psi_sus0 is None:
            out.append("sustained-load factor psi_sus0")
        if not s.psi_c:
            out.append("concrete strength factor psi_c")
        if kind == "rebar" and not s.f_bd:
            out.append("design bond strength f_bd (EAD 330087 / ETA-Rebar)")
        if not (s.eta or s.eads):
            out.append("ETA / assessment number")
        return out


def parse_spec_text(pages: Sequence[str], name: str = "", doc_name: str = "", source: str = "attached TDS",
                    url: str = "") -> ParseResult:
    spec = AdhesiveSpec(name=name or "")
    res = ParseResult(spec=spec)
    full = "\n".join(pages)
    doc = url or doc_name

    def prov(page: int, quote: str, conf: float = 0.85, note: str = "") -> Provenance:
        return Provenance(source=source, document=doc, page=page, quote=quote.strip()[:220],
                          confidence=conf, note=note)

    # ---------------------------------------------------------------- identity & approvals
    if not spec.name:
        m = re.search(r"(?:Injection system|Trade name:?)\s*\n?\s*(?:Injection system\s+)?([A-Za-z][^\n]{3,60})", full)
        if m:
            spec.name = re.sub(r"\s+", " ", m.group(1)).strip()
    for m in re.finditer(r"ETA\s*[-–]?\s*((?:\d\s*){2})\s*/\s*((?:\d\s*){4})", full):
        yy, nnnn = re.sub(r"\s", "", m.group(1)), re.sub(r"\s", "", m.group(2))
        eta = "ETA-%s/%s" % (yy, nnnn)
        if eta not in spec.eta:
            spec.eta.append(eta)
    for m in re.finditer(r"\bEAD\s*(\d{6})(?:\s*-\s*(\d{2})\s*-\s*(\d{4}))?", full):
        ead = f"EAD {m.group(1)}"
        if ead not in spec.eads:
            spec.eads.append(ead)
    for m in re.finditer(r"\b(ETAG\s*0?01|TR\s*0?(23|29|45|49|69))\b", full, re.I):
        t = re.sub(r"\s+", " ", m.group(0)).upper()
        if t not in spec.eads:
            spec.eads.append(t)
    for m in re.finditer(r"seismic performance categor(?:y|ies)\s*(C[12])", full, re.I):
        c = m.group(1).upper()
        if c not in spec.seismic:
            spec.seismic.append(c)
    if re.search(r"\bC1\b.*\bC2\b", full[:5000] if len(full) > 5000 else full) and "seismic" in full.lower():
        for c in ("C1", "C2"):
            if c not in spec.seismic and re.search(rf"seismic[^\n]{{0,60}}\b{c}\b", full, re.I):
                spec.seismic.append(c)
    spec.as5216_prequalified = True if ("EAD 330499" in spec.eads or re.search(r"AS\s*5216", full)) else None
    spec.rebar_qualified = True if any(e in ("EAD 330087", "TR 23", "TR 023") for e in spec.eads) else None
    if re.search(r"\bepoxy\b", full, re.I):
        spec.chemistry = "epoxy"
    elif re.search(r"vinyl\s?ester", full, re.I):
        spec.chemistry = "vinylester"
    elif re.search(r"\bhybrid\b", full, re.I):
        spec.chemistry = "hybrid"
    elif re.search(r"polyester", full, re.I):
        spec.chemistry = "polyester"
    if re.search(r"overhead|upward", full, re.I):
        spec.overhead_ok = True
    if re.search(r"flooded|water[- ]filled", full, re.I):
        spec.flooded_hole_ok = True
    if re.search(r"diamond", full, re.I):
        spec.diamond_drilling_ok = True
    if re.search(r"\bfire\b", full, re.I):
        spec.fire_data = True
    m = re.search(r"(?:working|service) life[^\n]{0,40}?(\d{2,3})\s*years|(\d{2,3})\s*years?\s*(?:working|service) life", full, re.I)
    if m:
        spec.working_life_years = int(m.group(1) or m.group(2))
    if spec.eta or spec.eads:
        spec.provenance["approvals"] = prov(1, f"{', '.join(spec.eta)} {', '.join(spec.eads)}", 0.9)

    # ---------------------------------------------------------------- table parsing
    entries: Dict[Tuple, BondEntry] = {}
    inst: Dict[Tuple[str, float], Dict[str, float]] = {}
    sizes: List[float] = []
    fastener = "rod"
    family: Optional[str] = None            # set by "Table ..." captions; None = decide from header label
    hef_marks: List[Tuple[int, int, List[float], str]] = []
    ctx_lines: List[str] = []
    state_cr: Optional[str] = None
    life = 50
    sus_window = 0
    for pno, ptxt in enumerate(pages, start=1):
        sus_window = 0
        for line_no, raw in enumerate(ptxt.split("\n")):
            line = re.sub(r"\s+", " ", raw).strip()
            if not line:
                continue
            low = line.lower()
            # --- table family: ETA tables for HIS sleeves / HZA tension anchors must not pollute rod/rebar data
            if re.match(r"table\s+[A-Z]?\d+", line, re.I):
                life = 50
                state_cr = None
                ctx_lines = []
                if re.search(r"\bHIS\b|HIS-|sleeve|\bHZA|tension anchor", line, re.I):
                    family = "other"
                elif re.search(r"reinforcing bar|rebar", line, re.I):
                    family = "rebar"
                elif re.search(r"threaded rod|\bHAS\b|HIT-V|stud|anchor rod", line, re.I):
                    family = "rod"
                continue
            # --- header (size row)
            hm = HEADER_RE.search(line)
            if hm and (not UNIT_BRACKET.search(line) or DIA_LABEL.search(line)):
                toks = hm.group(1).split()
                try:
                    vals = [_size(t) for t in toks]
                except ValueError:
                    vals = []
                if len(vals) >= 3 and all(b > a for a, b in zip(vals, vals[1:])) and 5 <= vals[0] and vals[-1] <= 60:
                    sizes = vals
                    label = line[:hm.start()].lower()
                    if re.search(r"\bhis\b|his-|sleeve|\bhza|tension anchor", label):
                        fastener = "other"
                    elif family == "other" and not re.search(r"threaded rod|reinforcing bar", label):
                        fastener = "other"
                    elif re.search(r"rebar|reinforcing|\bbar\b", label) and not re.search(r"threaded", label):
                        fastener = "rebar"
                    elif toks[0].upper().startswith("M") or family == "rod":
                        fastener = "rod"
                    elif family == "rebar":
                        fastener = "rebar"
                    continue
            # --- context
            m = re.search(r"service life of (\d+) years", low)
            if m:
                life = int(m.group(1))
                ctx_lines = []
                state_cr = None
                continue
            if re.search(r"\b(un|non)[- ]?cracked concrete", low) and not re.search(r"\[", line):
                state_cr = "ucr"
                ctx_lines = [low]
                continue
            if re.search(r"(?<!un)(?<!non)(?<!non-)\bcracked concrete", low) and not re.search(r"\[", line):
                state_cr = "cr"
                ctx_lines = [low]
                continue
            if low.startswith(("in ", "and ")) and re.search(r"drill|cored|hole", low):
                ctx_lines.append(low)
                continue
            if "sustained" in low or re.search(r"\bsus\b", low):
                sus_window = 8
            # --- factor patterns
            m = re.search(rf"k\s*cr\s*,\s*N\s*\[-\]\s*({NUM})", line)
            if m and spec.k_cr is None:
                spec.k_cr = fnum(m.group(1))
                spec.provenance["k_cr"] = prov(pno, line)
            m = re.search(rf"k\s*ucr\s*,\s*N\s*\[-\]\s*({NUM})", line)
            if m and spec.k_ucr is None:
                spec.k_ucr = fnum(m.group(1))
                spec.provenance["k_ucr"] = prov(pno, line)
            m = re.search(r"C(\d{2})\s*/\s*(\d{2})\s+(\d[.,]\d{1,3})\b", line)
            if m:
                f = fnum(m.group(3))
                fc = float(m.group(1))
                if 0.9 <= f <= 1.4 and fc not in spec.psi_c:
                    spec.psi_c[fc] = f
                    spec.psi_c.setdefault(20.0, 1.0)
                    spec.provenance.setdefault("psi_c", prov(pno, line, 0.8))
            if sus_window:
                sus_window -= 1
                for mm in re.finditer(rf"(\d{{2,3}})\s*°\s*C\s*/\s*(\d{{2,3}})\s*°\s*C\s+(0[.,]\d{{1,3}})\b", line):
                    v = fnum(mm.group(3))
                    if 0.3 <= v <= 1.0:
                        spec.psi_sus0_map[f"{mm.group(1)}/{mm.group(2)}"] = v
                if spec.psi_sus0_map:
                    spec.psi_sus0 = min(spec.psi_sus0_map.values())
                    spec.provenance.setdefault("psi_sus0", prov(pno, line, 0.75,
                                               "lowest value over temperature ranges (conservative)"))
            if "inst" in low and "[-]" in line:
                tail = line.split("[-]", 1)[1]
                nums = [fnum(x) for x in re.findall(NUM, tail)]
                if nums:
                    v = max(nums)
                    if "water" in low or "flooded" in low:
                        key = "flooded"
                    elif "roughen" in low:
                        key = None
                    elif "diamond" in low:
                        key = "diamond"
                    elif re.search(r"^hammer drilling\b", low) and "hollow" not in low:
                        key = "hammer"
                    else:
                        key = None
                    if key:
                        spec.gamma_inst_map[key] = max(v, spec.gamma_inst_map.get(key, 0.0))
                        spec.provenance[f"gamma_inst_{key}"] = prov(pno, line, 0.8)
                        if key == "hammer":
                            spec.gamma_inst = spec.gamma_inst_map["hammer"]
            if re.search(r"effective embedment depth", low) and sizes and fastener in ("rod", "rebar"):
                hef_marks.append((pno, line_no, list(sizes), fastener))
            # --- data rows aligned with the size header
            rm = ROW_RE.match(line)
            if not rm or not sizes:
                continue
            label, tail = rm.group(1), rm.group(2)
            toks = tail.split()
            if len(toks) != len(sizes) or fastener == "other":
                continue
            vals = [None if t in ("-", "–", "—") else fnum(t) for t in toks]
            bm = re.search(r"R\s?k\s*,\s*(ucr|cr)\b", label)
            if bm and re.search(r"N/mm", label):
                tag = bm.group(1)
                block = " ".join(ctx_lines)
                if "water" in block or "flooded" in block:
                    cond, drill = "flooded", "hammer"
                elif "diamond" in block and "hammer" not in block:
                    cond, drill = "dry_wet", "diamond"
                else:
                    cond, drill = "dry_wet", "hammer"
                tm = re.search(r"temperature range\s*([IVX]+|\d)\s*:?\s*(\d{2,3})\s*°\s*C\s*/\s*(\d{2,3})\s*°\s*C", label, re.I)
                if tm:
                    trange = f"{tm.group(1).upper()}: {tm.group(2)}°C/{tm.group(3)}°C"
                    tkey = f"{tm.group(2)}/{tm.group(3)}"
                else:
                    tm2 = re.search(r"(\d{2,3})\s*°\s*C\s*/\s*(\d{2,3})\s*°\s*C", label)
                    trange = f"{tm2.group(1)}°C/{tm2.group(2)}°C" if tm2 else "default"
                    tkey = f"{tm2.group(1)}/{tm2.group(2)}" if tm2 else ""
                if state_cr and state_cr != tag:
                    pass  # row label is authoritative
                for d, v in zip(sizes, vals):
                    if v is None:
                        continue
                    k = (fastener, d, cond, drill, tkey or trange, life)
                    e = entries.get(k)
                    if e is None:
                        e = BondEntry(d=d, fastener=fastener, condition=cond, drilling=drill, temp_range=trange,
                                      temp_key=tkey, service_life=life,
                                      prov=prov(pno, line, 0.88))
                        entries[k] = e
                    if tag == "ucr":
                        e.tau_ucr = v
                    else:
                        e.tau_cr = v
                res.n_bond_rows += 1
                continue
            # several labels can share one printed line ("... hmin [mm] hef + 30 ... Maximum torque Tmax [Nm] 10 20 ..."):
            # the numbers belong to the LAST label before them
            best, best_pos = None, -1
            for attr, pat in INSTALL_ROWS:
                for mm in pat.finditer(label):
                    if mm.start() >= best_pos:
                        best, best_pos = attr, mm.start()
            if best in INSTALL_BOND_ATTRS:
                for d, v in zip(sizes, vals):
                    if v is not None:
                        inst.setdefault((fastener, d), {})[best] = v

    # embedment ranges printed as multi-line cells ("60 / to / 160")
    for pno, line_no, sz, fam in hef_marks:
        blob = " ".join(l.strip() for l in pages[pno - 1].split("\n")[line_no: line_no + 60])
        pairs = re.findall(r"(\d{2,3})\s+to\s+(\d{2,3})", blob)
        if len(pairs) >= len(sz):
            for d, (a, b) in zip(sz, pairs[:len(sz)]):
                inst.setdefault((fam, d), {}).update({"hef_min": float(a), "hef_max": float(b)})
    # attach installation attributes
    for e in entries.values():
        for attr, v in inst.get((e.fastener, e.d), {}).items():
            setattr(e, attr, v)
    spec.bond = sorted(entries.values(), key=lambda e: (e.fastener, e.d, e.condition, e.drilling, e.temp_range, e.service_life))
    if any(e.tau_cr is not None for e in spec.bond):
        spec.cracked_concrete_ok = True

    m = re.search(r"1[.,]0\s*h\s*ef[\s\S]{0,60}?4[.,]6\s*h\s*ef\s*[-–]\s*1[.,]8\s*h[\s\S]{0,60}?2[.,]26\s*h\s*ef", full)
    if m:
        spec.c_cr_sp_rule_en = True
        spec.provenance["c_cr_sp"] = prov(1, re.sub(r"\s+", " ", m.group(0)), 0.8)

    # f_bd (post-installed rebar design bond strength)
    m = re.search(r"f\s*b\s*d[^\n]{0,80}?\n?([^\n]*)", full)
    if m and re.search(r"C\d{2}\s*/\s*\d{2}", full):
        hdr = re.search(r"((?:C\d{2}\s*/\s*\d{2}\s+){3,}C\d{2}\s*/\s*\d{2})", full)
        row = re.search(r"f\s*b\s*d[^\n\d]{0,60}((?:" + NUM + r"\s+){3,}" + NUM + ")", full)
        if hdr and row:
            fcs = [float(x) for x in re.findall(r"C(\d{2})\s*/", hdr.group(1))]
            vals = [fnum(x) for x in re.findall(NUM, row.group(1))]
            if len(fcs) == len(vals):
                spec.f_bd = dict(zip(fcs, vals))
                spec.provenance["f_bd"] = prov(1, row.group(0), 0.7)
                spec.rebar_qualified = spec.rebar_qualified or True

    res.found = {
        "bond": bool(spec.bond), "gamma_inst": bool(spec.gamma_inst_map or spec.gamma_inst),
        "psi_sus0": spec.psi_sus0 is not None, "psi_c": bool(spec.psi_c),
        "approvals": bool(spec.eta or spec.eads), "k_cr": spec.k_cr is not None,
        "f_bd": bool(spec.f_bd),
    }
    score = sum(res.found[k] for k in ("bond", "gamma_inst", "psi_sus0", "psi_c", "approvals"))
    res.confidence = round(score / 5.0, 2)
    for k, v in res.found.items():
        if not v:
            res.notes.append(f"'{k}' not found by the deterministic parser.")
    spec.docs.append({"title": doc_name or url or "document", "url": url, "kind": source})
    return res


# ----------------------------------------------------------------------------- LLM fallback
def number_in_text(v: float, text: str) -> bool:
    """True if `v` appears literally (dot or comma decimals) in `text`."""
    forms = {f"{v:g}", f"{v:.1f}", f"{v:.2f}", f"{int(v)}" if float(v).is_integer() else f"{v:g}"}
    forms |= {f.replace(".", ",") for f in forms}
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", text) for f in forms)


LLM_SYSTEM = ("You extract numeric design data for post-installed chemical anchors from technical data sheets. "
              "Return ONLY JSON. Never invent numbers: copy values exactly as printed.")


def llm_fill_missing(res: ParseResult, pages: Sequence[str], client, doc_name: str = "") -> List[str]:
    """Ask an LLM for bond data when the deterministic parser found none.  Returns notes."""
    notes: List[str] = []
    if res.spec.bond:
        return notes
    text = "\n".join(pages)
    # focus on the passages that mention bond resistance
    chunks = []
    for m in re.finditer(r"(bond|τ|tau|Rk,u?cr|pull-?out)", text, re.I):
        chunks.append(text[max(0, m.start() - 600): m.start() + 1400])
        if len(chunks) >= 3:
            break
    ctx = "\n---\n".join(chunks)[:9000]
    if not ctx:
        return ["LLM fallback: no passage mentioning bond resistance found."]
    prompt = (
        "From the text below, extract characteristic bond resistance values (N/mm2) for chemical anchors/rebar.\n"
        "Return JSON: {\"entries\":[{\"d\":<mm>,\"tau_cr\":<num|null>,\"tau_ucr\":<num|null>,"
        "\"condition\":\"dry_wet|flooded\",\"temp_range\":\"<text>\",\"quote\":\"<verbatim source snippet>\"}],"
        "\"gamma_inst\":<num|null>,\"psi_sus0\":<num|null>}\nTEXT:\n" + ctx)
    try:
        data = client.ask_json(LLM_SYSTEM, prompt)
    except Exception as e:  # network / format errors must not break the pipeline
        return [f"LLM fallback failed: {e}"]
    kept = 0
    for ent in (data.get("entries") or []):
        try:
            d = float(ent["d"])
        except Exception:
            continue
        vals = {k: ent.get(k) for k in ("tau_cr", "tau_ucr")}
        ok = all(v is None or number_in_text(float(v), text) for v in vals.values())
        if not ok or all(v is None for v in vals.values()) or not (1 <= (vals["tau_cr"] or vals["tau_ucr"]) <= 60):
            notes.append(f"LLM value rejected (not found verbatim in the document or implausible): {ent}")
            continue
        res.spec.bond.append(BondEntry(
            d=d, tau_cr=vals["tau_cr"], tau_ucr=vals["tau_ucr"], condition=ent.get("condition") or "dry_wet",
            temp_range=ent.get("temp_range") or "default",
            prov=Provenance(source="LLM-extracted (verified verbatim)", document=doc_name,
                            quote=str(ent.get("quote", ""))[:200], confidence=0.55)))
        kept += 1
    notes.append(f"LLM fallback accepted {kept} bond entries - ENGINEER MUST CONFIRM against the source.")
    res.found["bond"] = kept > 0
    return notes
