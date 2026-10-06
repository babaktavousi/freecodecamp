# AnchorCheck — structural review of chemical anchors & post-installed dowels (AS 5216 / AS 3600)

A local app (runs on your PC, uses the internet and/or a local AI model) that takes **structural drawings in PDF**,
**recognises chemical anchors / threaded rods / post-installed reinforcement dowels** from their notes *and* drawn shape,
**identifies the governing Australian Standard**, **finds and compares the adhesive's published specification**,
**checks the anchors against the proposed loads**, and **exports a professional PDF review report**.

> **Principle:** AI *reads and drafts* · deterministic code *calculates* · the engineer *verifies*.
> No number produced by a language model ever enters a calculation without passing through an editable review step,
> and every product value carries its source (document, page, quoted line).

```
 drawings.pdf ─► read notes (text) + read shapes (vision AI) ─► anchor/dowel groups ─► YOU review/edit
                                   │                                               │
 AS standard / TDS / ETA ─► knowledge base ─► adhesive spec (attached → library → internet) ─► compare with design needs
                                                                                    │
                       AS 5216 / AS 3600 checks (cone, bond, steel, shear, pry-out, edge, interaction, detailing)
                                                                                    │
                       review findings + fix suggestions ─► PDF report (calcs, sources, standards, sign-off block)
```

## Quick start

Requires **Python 3.9+** (Windows, macOS or Linux).

```bash
cd anchorcheck
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m anchorcheck ui                                   # opens http://localhost:8501
```

Windows: double-click `run_app.bat`. macOS/Linux: `./run_app.sh`.

Try it with the bundled fictitious drawing: `python samples/make_sample_drawing.py`, then upload
`samples/sample_structural_drawing.pdf` in the app.

Headless (no browser):

```bash
python -m anchorcheck analyse drawing.pdf --docs AS5216.pdf HIT-RE500V3_ETA.pdf --out report.pdf --project "Warehouse ext." --fc 32
python -m anchorcheck verify          # reproduces published manufacturer / AEFAC benchmark values with the engine
```

## AI models (all optional)

Without a model the app still works: notes are read by rule-based parsing and you complete the rest in the review tab.
With a model it also **recognises anchors from the drawn shapes** and can rescue badly formatted data sheets.

| Provider | Setup |
|---|---|
| **Ollama** (local, private) | `ollama serve`, then e.g. `ollama pull qwen2.5vl:7b` (vision) and `ollama pull llama3.1:8b` (text). Optional `ollama pull nomic-embed-text` for semantic search of attached documents. Select *ollama* in the sidebar. |
| **Anthropic** | set `ANTHROPIC_API_KEY`, pick a model (e.g. `claude-sonnet-5-5`). |
| **OpenAI-compatible** | any endpoint (OpenAI, LM Studio, vLLM, OpenRouter…): set base URL, model, key. |

Privacy: with Ollama nothing leaves your PC except web *search queries containing product names*. With a cloud provider, drawing
page images/text go to that provider. **Offline mode** (sidebar) disables all internet access.

## Attaching standards and data sheets ("training")

Attach your licensed **AS 5216 / AS 3600** PDFs and the adhesive's **TDS / European Technical Assessment (ETA)** in tab 1.
They are chunked and indexed locally (BM25, plus Ollama embeddings if configured) in `~/.anchorcheck/kb`. This is *retrieval*, not weight
fine-tuning, which is what you want for auditable engineering work:

* attached TDS/ETA are parsed **first**; the internet is only searched when nothing is attached;
* the report cites the relevant passage (document + page) of the attached standard next to each check;
* nothing changes in any model; delete a document and it is gone.

AS standards are copyrighted: the app never ships clause text, only uses what *you* attach.

## Adhesive recognition & specification lookup

The product name is recognised from the drawing notes (Ramset ChemSet/Epcon, Hilti HIT, Sika AnchorFix, fischer FIS, Simpson SET/AT,
Powers/DEWALT…; unknown names are flagged, never guessed). The specification is resolved in this order:

1. engineer-confirmed entry in your saved library → 2. attached TDS/ETA → 3. earlier unconfirmed library entry →
4. internet (manufacturer domains ranked first, low-trust aggregators skipped; DuckDuckGo with no key, or Brave/SearXNG) →
5. manual entry.

The parser reads bond resistance τ<sub>Rk</sub> (cracked/uncracked, by temperature range, hole type, drilling method, 50/100-year),
γ<sub>inst</sub>, ψ<sub>sus</sub>, ψ<sub>c</sub>, installation limits, approvals (ETA / EAD 330499 / EAD 330087), seismic categories — and it was tested on a **real
ETA** (Hilti ETA‑16/0143), including its awkward layout (letter‑spaced numbers, decimal commas, lost τ glyph, HIS‑sleeve tables that share
the same size header). LLM-extracted numbers are accepted only if they appear verbatim in the source text.

Then it compares **what the design needs** (cracked concrete, size/embedment range, hole condition, drilling, overhead, seismic, sustained load,
rebar qualification) with **what the product documents provide**, and screens **alternative adhesives** by re-running the same design with each.

## What is checked

* **Threaded rods / studs (AS 5216:2021 bonded-anchor method, EN 1992-4 / EAD 330499 basis):** steel (tension, shear incl. lever arm),
  combined pull-out & concrete failure with group/edge/eccentricity effects, concrete cone (incl. 3-edge rule), splitting (uncracked),
  sustained load, pry-out, concrete edge failure per edge with direction factor, N+V interaction (both the power form and the 1.2 linear form),
  min. spacing/edge/thickness/embedment, fixture clearance holes. Elastic force distribution for N, M<sub>x</sub>, M<sub>y</sub>, V on rigid plates.
* **Post-installed reinforcing bars (AS 5216:2021 Appendix D.4.1 + AS 3600 cl 13.1.2):** development length with k<sub>1</sub>,k<sub>2</sub>,k<sub>3</sub>,
  f<sub>bd</sub> adjustment, partial development (σ<sub>st</sub> = f<sub>sy</sub>·L/L<sub>sy.t</sub>, ≥ 12d<sub>b</sub>), EAD 330087 cover/spacing rules,
  with a cross-check treating the bar as an anchor.
* **Standards identification:** AS 5216:2021 (primary) with AS 3600, AS/NZS 1170.0, AS/NZS 4671, AS 4100, AS 1170.4, AS 5100.5, AS 3700/AS 1720.1
  (substrate not concrete ⇒ AS 5216 *not* applicable) — with reasons, superseded‑citation warnings (SA TS 101, AS 5216:2018, AS 3600:2009…) and
  a note when the drawing doesn't cite AS 5216.
* **Drawing review:** missing notes (hole cleaning, installer competence AS 5216 App. B, proof testing, scanning for reinforcement, cure/temperature,
  concrete grade, product equivalence), RFIs for everything assumed, **fix suggestions** from a sensitivity study (embedment, size, grade, more anchors,
  edge distance, combinations — honestly labelled when they still do not pass).

## Engineering confidence — read this

See **[docs/ENGINEERING_BASIS.md](docs/ENGINEERING_BASIS.md)**. In short:

* The engine reproduces published values from Ramset's AS 5216-based design tables (cone, pry-out, edge shear), AEFAC Technical Note TN‑08 worked
  examples and Ramset's rebar development-length tables (`python -m anchorcheck verify`; 120+ automated tests).
* AS 5216:2021 is a paid standard that could not be read for this build. Clause numbers are therefore cited only where confirmed from public
  sources (Appendix D, F, B2, E); **the report prints every factor used (Appendix A) with a "cross-checked / confirm" flag** so a reviewer can check
  them against the licensed copy quickly.
* Static ULS only. **Not designed:** seismic (App. F), fire, fatigue, anchor channels, masonry/timber, torsion, plate bending, rebar-lap with new bars,
  shear-friction across joints. The tool flags these instead of ignoring them.
* Results are labelled **PROVISIONAL** (with a watermark) whenever data is assumed, missing, or not yet confirmed by the engineer. A *flat* "ADEQUATE" is
  only possible when every check passes on engineer-confirmed product data and a stated concrete grade.

## Using it as an engineer

1. **Upload** drawings (+ optional AS standard / TDS / ETA).
2. **Review data** — everything extracted is editable and shows its source; RFIs list what the drawing does not say.
3. **Standards** — see the governing standard(s) and why.
4. **Adhesive** — specification found (attached/library/internet), compare with design needs, confirm values, save to your library.
5. **Analysis** — run checks; open any check to see formula, inputs, R<sub>k</sub>, φ, R<sub>d</sub>, utilisation.
6. **Report** — fill project/checker details → download the PDF (cover, contents, summary, standards, extracted data, product assessment, worked
   calculations, findings, factor appendix, notes, extracts, sign-off block, drawing thumbnails).

## Project layout

```
anchorcheck/
  design/      anchors.py (AS 5216 engine) · rebar.py (App D) · geometry.py · basis.py (all factors + provenance) · steel.py
  extract/     patterns.py (notes) · vision.py (shape recognition) · build.py (cases, RFIs, merge)
  products/    registry.py (names) · tds.py (TDS/ETA parser) · web.py · lookup.py · compare.py · library.py
  standards/   registry.py · identify.py
  knowledge/   store.py (attached-document index)       llm/ clients.py (Ollama/Anthropic/OpenAI-compat)
  report/      builder.py (ReportLab) · figures.py      app/ streamlit_app.py        cli.py · pipeline.py · review.py
tests/         engine benchmarks · parser on a real ETA excerpt · extraction · vision (fake model) · LLM request shapes · report smoke tests
samples/       make_sample_drawing.py
```

Add or correct a product: attach its TDS/ETA, or use *Enter / override values manually* in tab 4 and *Save to library*.
Change a code factor: edit `DesignBasis` (all factors live in `design/basis.py`).

## Licences of bundled/used components

ReportLab (BSD), pypdfium2 (Apache-2.0/BSD-3), Streamlit (Apache-2.0), Requests (Apache-2.0). The PDF report bundles **DejaVu Sans** fonts
(free licence, `anchorcheck/report/fonts/LICENSE-DejaVu.txt`).

## Disclaimer

Software-assisted review aid. It does not replace the responsible registered/chartered structural engineer, who must verify the inputs, the product
data and the results before reliance. Installation must follow the manufacturer's instructions and AS 5216:2021 Appendix B.
