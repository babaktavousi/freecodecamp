# Engineering basis, verification and open items

This document states precisely **what the engine computes, what it has been checked against, and what a reviewer must still confirm**.

## 1. Methodology

### 1.1 Bonded (chemical) anchors — threaded rods

AS 5216:2021 adopts the EN 1992-4 / EAD 330499 method for post-installed bonded anchors with capacity reduction factors φ = 1/γ<sub>M</sub> (the
reciprocal of the partial factors, which the product's assessment qualifies). For each load combination the engine verifies:

| Mode | Resistance (characteristic) | φ |
|---|---|---|
| Steel, tension | N<sub>Rk,s</sub> = A<sub>s</sub> f<sub>uk</sub> (lesser of ETA value and this) | 1/max(1.2 f<sub>uk</sub>/f<sub>yk</sub>, 1.4) |
| Steel, shear | V<sub>Rk,s</sub> = k<sub>7</sub> k<sub>6</sub> A<sub>s</sub> f<sub>uk</sub> (k<sub>6</sub> = 0.6 for f<sub>uk</sub> ≤ 500 else 0.5); lever-arm form α<sub>M</sub> M⁰<sub>Rk,s</sub>/l with M⁰ = 1.2 W<sub>el</sub> f<sub>uk</sub>(1 − N<sub>Ed</sub>/N<sub>Rd,s</sub>); ×0.8 for a grout pad ≤ d/2 | 1/max(f<sub>uk</sub>/f<sub>yk</sub>, 1.25) |
| Combined pull-out & concrete | N⁰<sub>Rk,p</sub> = π d h<sub>ef</sub> τ<sub>Rk</sub> ψ<sub>c</sub>; s<sub>cr,Np</sub> = 7.3 d √τ<sub>Rk,ucr</sub> ≤ 3h<sub>ef</sub>; × A<sub>p,N</sub>/A⁰<sub>p,N</sub> · ψ<sub>g,Np</sub> · ψ<sub>s,Np</sub> · ψ<sub>ec,Np</sub> | 1/(γ<sub>c</sub> γ<sub>inst</sub>), γ<sub>c</sub> = 1.5 |
| Concrete cone | N⁰<sub>Rk,c</sub> = k √f′c h<sub>ef</sub><sup>1.5</sup> (k = 7.7 cracked, 11.0 uncracked); × A<sub>c,N</sub>/A⁰<sub>c,N</sub> · ψ<sub>s,N</sub> · ψ<sub>re,N</sub> · ψ<sub>ec,N</sub>; h′<sub>ef</sub> rule for ≥ 3 edges | as above |
| Splitting (uncracked) | N⁰<sub>Rk,sp</sub> = min(N<sub>Rk,p</sub>, N⁰<sub>Rk,c</sub>) with c<sub>cr,sp</sub> = 1.0h<sub>ef</sub> / 4.6h<sub>ef</sub>−1.8h / 2.26h<sub>ef</sub>; ψ<sub>h,sp</sub> = 1 | as above |
| Sustained load | N<sub>Ed,sus</sub> ≤ ψ⁰<sub>sus</sub> N<sub>Rd,p</sub> | — |
| Pry-out | V<sub>Rk,cp</sub> = k<sub>8</sub> min(N<sub>Rk,c</sub>, N<sub>Rk,p</sub>), k<sub>8</sub> = 2 (h<sub>ef</sub> ≥ 60 mm) | γ<sub>c</sub>γ<sub>inst</sub> |
| Concrete edge | V⁰<sub>Rk,c</sub> = k<sub>9</sub> d<sup>α</sup> l<sub>f</sub><sup>β</sup> √f′c c<sub>1</sub><sup>1.5</sup>; α = 0.1(l<sub>f</sub>/c<sub>1</sub>)<sup>0.5</sup>, β = 0.1(d/c<sub>1</sub>)<sup>0.2</sup>; k<sub>9</sub> = 1.7 cracked / 2.4 uncracked; × A<sub>c,V</sub>/A⁰<sub>c,V</sub> ψ<sub>s,V</sub> ψ<sub>h,V</sub> ψ<sub>α,V</sub> | as above |
| Interaction | steel β<sub>N</sub>² + β<sub>V</sub>² ≤ 1; concrete β<sub>N</sub><sup>1.5</sup> + β<sub>V</sub><sup>1.5</sup> ≤ 1; (linear β<sub>N</sub>+β<sub>V</sub> ≤ 1.2 reported) | — |

Anchor forces: elastic distribution on a rigid plate considering anchors only (plate bearing neglected ⇒ conservative lever arm).
Edge shear: the front row is assumed to carry the full shear (conservative). Concrete is treated as **cracked** unless the drawing says otherwise.

### 1.2 Post-installed reinforcing bars

AS 5216:2021 Appendix D (D.4.1): L<sub>sy.t</sub> = max(0.5 k<sub>1</sub> k<sub>3</sub> f<sub>sy</sub> d<sub>b</sub> / (k<sub>2</sub> √f′c), 0.058 f<sub>sy</sub> k<sub>1</sub> d<sub>b</sub>) from
AS 3600 cl 13.1.2.2 (k<sub>2</sub> = (132 − d<sub>b</sub>)/100, k<sub>3</sub> = 1 − 0.15(c<sub>d</sub> − d<sub>b</sub>)/d<sub>b</sub> ∈ [0.7, 1.0]), increased in proportion if the adhesive's
EAD 330087 design bond strength f<sub>bd</sub> is below the table value (20→2.3, 25→2.7, 32→3.2, 40→3.7, 45→4.0, 50→4.3 MPa; × (132−d<sub>b</sub>)/100 for d<sub>b</sub> > 32 mm).
Partial development: L<sub>st</sub> = L<sub>sy.t</sub> σ<sub>st</sub>/f<sub>sy</sub> ≥ 12 d<sub>b</sub>; N* ≤ 0.8 A<sub>s</sub> σ<sub>st</sub>. Cover c<sub>min</sub> = 30 + 0.06L ≥ 2d<sub>b</sub> (hammer/diamond, d<sub>b</sub> < 25),
40 + 0.06L (≥ 25), 50 + 0.08L / 60 + 0.08L (compressed air); s<sub>min</sub> = max(40, 4d<sub>b</sub>).
k<sub>1</sub> = 1.0 (AEFAC TN‑08 note 2).

## 2. Verification against published data

All reproduced by `python -m anchorcheck verify` and by `tests/`:

| Item | Published | Engine | Source |
|---|---|---|---|
| Concrete cone, uncracked, f′c 32, h<sub>ef</sub> 70 / 80 / 100 mm (φ = 1/1.5) | 24.3 / 29.7 / 41.5 kN | 24.3 / 29.7 / 41.5 | Ramset Specifiers Anchoring Resource Book (SARB), ChemSet Reo 502 Xtrem, Table 2a |
| Cracked/uncracked cone ratio | 0.70 | 7.7/11.0 = 0.70 | SARB Table 2a‑2 |
| Pry-out, h<sub>ef</sub> 90 / 110 mm | 70.8 / 95.7 kN | 70.8 / 95.7 | SARB Table 4e |
| Edge shear M10 c 40 / M12 c 40 / M16 c 45 | 4.3 / 4.7 / 6.2 kN | 4.3 / 4.7 / 6.2 | SARB Table 4a‑1 |
| Group spacing factor | 0.5 + a/(6h) | identical (A<sub>c,N</sub>/A⁰<sub>c,N</sub>) | SARB Table 2d |
| Direction factor ψ<sub>α,V</sub> | 1 / 1.1 / 1.2 / 1.5 / 2 at ≤55° / 60° / 70° / 80° / ≥90° | 1 / 1.07 / 1.23 / 1.50 / 2 | SARB Table 4c |
| γ<sub>Ms</sub> | 1.5 (5.8, 8.8), 1.87 (A4‑70) | 1.5 / 1.5 / 1.87 | SARB; Hilti ETA‑16/0143 Table C1 |
| k<sub>cr</sub>, k<sub>ucr</sub>, c<sub>cr,N</sub>, s<sub>cr,N</sub>, splitting c<sub>cr,sp</sub> rule | 7.7, 11.0, 1.5h<sub>ef</sub>, 3h<sub>ef</sub>, 1.0/4.6−1.8h/2.26 | identical | Hilti ETA‑16/0143 Table C1 |
| Nominal development length N12…N40, f′c 32 | 350, 465, 580, 700, 990, 1345 mm | 348, 464, 580, 696, 990, 1345 | SARB rebar chapter Table 2 |
| TN‑08 Examples 1 & 2 (N12) | 350 mm; 348 mm (min. governs) | 350; 348 | AEFAC TN‑08 v1.1 |
| N16 steel design capacity | 80.4 kN | 80.4 | SARB rebar table |
| TDS parser on real ETA‑16/0143 | published τ<sub>Rk</sub> rows (rods and rebar, cracked/uncracked/diamond/flooded, two temperature ranges) | row‑for‑row equal | Hilti ETA‑16/0143 Annex C |

## 3. Items a reviewer must still confirm (not verifiable without the licensed AS 5216:2021)

1. **φ for shear steel** — engine uses 1/max(f<sub>uk</sub>/f<sub>yk</sub>, 1.25) (EN 1992‑4); Ramset tabulates 0.67 for stud shear. Check AS 5216 Table of capacity reduction factors.
2. **γ<sub>inst</sub> default (1.4)** is used only when the assessment gives none; the report flags it and marks failures that depend on it "provisional".
3. **ψ⁰<sub>g,Np</sub> constants** (k = 2.3 cracked / 3.2 uncracked) use f′c (cylinder); EN uses cube strength — conservative.
4. **Splitting** ψ<sub>h,sp</sub> taken as 1.0 (conservative).
5. **Clause numbers**: only Appendix B (installer competence), D (post-installed rebar, D.4.1/D.4.2, Table D.4), E (redundant non-structural), F (seismic, Tables F.3.1/F.3.2) were confirmed from public sources;
   check results cite "EN 1992-4 / AS 5216:2021" by topic rather than inventing AS clause numbers.
6. **Edition status** of AS 3600:2018, AS 4100:2020, AS/NZS 1170.0:2002, AS 1170.4 (amendments / current edition).
7. **Shear steel parameters** — k<sub>6</sub> (0.6 / 0.5), k<sub>7</sub> = 1.0 (ductile steel), the 0.8 grout-pad factor, lever arm l = e<sub>1</sub> + d/2 with e<sub>1</sub> = stand-off + t<sub>fixture</sub>/2, and
   α<sub>M</sub> = 1 (free) / 2 (fixed) follow EN 1992‑4 practice; where the product's assessment gives V<sup>0</sup><sub>Rk,s</sub> it should replace them.
8. Rebar designed by **D.4.2 (bond-splitting, EAD 332402)** is not implemented; D.4.1 is.

## 4. Public sources used while building this tool

* AS 5216:2021 overview: Ramset "AS 5216:2021 COMPLIANCE" data sheet (published 23 July 2021; replaces AS 5216:2018; referenced in NCC 2022); Pokharel et al., *What is new in AS 5216:2021 and why it matters*, Concrete 2021.
* AEFAC Technical Notes TN‑01 (design concepts) and TN‑08 (post-installed rebar connections, v1.1, 2019).
* Ramset Specifiers Anchoring Resource Book excerpts (Reo 502 Xtrem anchor studs; rebar to AS 5216 / AS 3600 / EOTA TR 069).
* Hilti HIT‑RE 500 V3, ETA‑16/0143 (CSTB, 14/05/2019), Annexes B and C — used to test the data-sheet parser.

## 5. Not covered (flagged in the app and the report)

Seismic design (AS 5216 App. F; the tool lists the product's C1/C2 qualification only), fire, fatigue/impact, anchor channels, redundant non-structural
fixings (App. E), masonry/timber substrates, torsion, flexible plates/prying, plate and weld design (AS 4100), lap splices with new bars (AS 3600 cl 13.2),
shear friction/dowel action across construction joints (AS 3600 cl 8.4), corrosion/durability selection (AS/NZS 2312.2).
