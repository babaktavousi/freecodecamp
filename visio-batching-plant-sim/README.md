# Batching Plant Simulator for Microsoft Visio

Reads a Visio drawing of a **concrete / cement batching plant**, verifies that the model is complete (rules **plus a
free AI check**), walks you through corrections step by step, asks for each equipment's capacity and rates in a
dialog, then runs a **discrete-event simulation** to find the **bottleneck**, the **overall production capacity** and
**what to modify** so the plant runs smoothly.

```
 Visio drawing ─► 1 Model ─► 2 Verification ─► 3 Equipment data ─► 4 Simulation ─► 5 Bottlenecks & plan
 (IDs + connectors)   │        rules + AI          (dialog)            (DES)        (what-if re-simulation)
                      └─ corrections one step at a time, then final re-verification
```

## Modules

| # | Module | What it does |
|---|--------|--------------|
| 1 | **Model** | Reads equipment IDs/types and glued connectors from the open Visio page (COM), a `.vsdx` file, or JSON. Unrecognised shapes, duplicate IDs and loose connector ends are reported. Wrong types can be changed; IDs/types can be written back to the shapes' *Shape Data*. |
| 2 | **Verification** | Deterministic rules (required equipment, valid connections, weigh hopper before mixer, every material reaches the mixer, mixer reaches a truck, no loops/orphans, PLC links) **and** an AI audit through a free OpenAI-compatible API. Defects become an ordered list of **correction steps** (apply / skip / apply all, optionally drawn into Visio). After the last step a **final verification** re-runs rules + AI. Equipment data is locked until the model is verified. |
| 3 | **Equipment data** | Modal dialog that asks, item by item, for rate (t/h, m³/h), capacity, weigh/dump/mix/discharge times and number of parallel units, with type-aware suggested values and validation. Also the mix recipe (kg/m³). Simulation unlocks when every item is confirmed. |
| 4 | **Simulation** | Discrete-event simulation (own event kernel). Batches flow: source → conveyor/screw/pump → weigh hopper (filled while the chain is held, blocked if the previous batch is still in the hopper) → mixer (dump, mix, discharge) → discharge hopper → truck bay. Parallel units, work-in-process limit, random ±variability, replications with 95 % CI. |
| 5 | **Bottlenecks & plan** | Busy / blocked % for every item, "plant limit if this were the only constraint", findings (store autonomy, batch size limited by a hopper, oversized items…) and a **modification plan**: every candidate change (faster rate, bigger hopper, shorter time, extra parallel unit) is re-simulated; the best small change is applied, the next bottleneck is found, repeat until no worthwhile gain or the target is reached. Shapes in Visio can be coloured by load. |

## Drawing conventions (what the reader recognises)

Best: give each equipment shape *Shape Data* rows **EquipID** (e.g. `CSL-01`), **EquipType** (e.g. `cement_silo`) and
optionally **Material** (`sand`, `gravel`, `cement`, `water`, `admixture`) — *Write IDs/types to Visio shapes* does this
for you. Otherwise the ID is taken from the shape text and the type from the ID prefix or words in the text/master name.

| Prefix | Type | | Prefix | Type |
|---|---|---|---|---|
| AGB | aggregate bin | | WGH | weigh hopper |
| CSL | cement silo | | MIX | mixer |
| WTK | water tank | | DCH | discharge / holding hopper |
| ADT | admixture tank | | TRK | truck loadout bay |
| CNV / SCW / PMP | belt conveyor / screw conveyor / pump | | CTL / DCL | PLC / dust collector |

Connect shapes with **glued connectors**; arrow direction = material flow (begin → end). Connectors touching the
controller (CTL) or dust collector are control signals. Typical line:
`bin → belt → weigh hopper → mixer → discharge hopper → truck`, `silo → screw → weigh hopper → mixer`,
`tank → pump → weigh hopper → mixer`, `PLC ⇢ mixer / hoppers`.

## Install & run (Windows with Visio)

1. Install Python 3.10+ (tkinter is included) and `pip install pywin32`.
2. Copy this folder to `C:\BatchingPlantSim` (or anywhere; edit `INSTALL_DIR` in step 3).
3. In Visio: `Alt+F11` → *File ▸ Import File…* → `visio\BatchingPlantSim.bas`. Run macro **RunBatchingPlantSim**
   (Developer ▸ Macros, or add it to the Quick Access Toolbar). Or simply run `python main.py --live` with the drawing open.
4. Without live Visio: `python main.py samples\sample_plant_defective.vsdx` (any `.vsdx` or `.json`; save `.vsd` as `.vsdx` first).

Headless pipeline (also works on Linux/macOS):
```
python main.py cli samples/sample_plant_defective.vsdx --auto-fix --defaults --no-ai
python main.py cli samples/sample_plant_complete.json --target 90 --out report.json
```

## Free AI check

Any OpenAI-compatible chat endpoint. *AI settings…* has presets:

| Provider | Key | Notes |
|---|---|---|
| `pollinations` (default) | none | shared anonymous free tier; may answer 402/429 when its quota is used up |
| `groq`, `gemini`, `openrouter` | free key | recommended for reliable use (set the key in *AI settings*, stored in `config.json`, which is git-ignored) |
| `ollama` | none | local model, fully offline |

Or use env vars `BPSIM_AI_PROVIDER / BPSIM_AI_URL / BPSIM_AI_KEY / BPSIM_AI_MODEL`. The AI never changes the model
directly: its suggested steps are validated (known types, existing IDs, must not add rule errors) and still go through
your Apply/Skip. If the AI is unreachable, verification continues on rules alone and the report says so. If the AI says
"incomplete" while the rules pass, you can *Accept despite AI remarks*.

## Simulation assumptions (read before trusting the numbers)

* Demand is unlimited (a truck is always waiting); the output is the **capacity**, not a forecast of demand.
* Batch size = min(mixer batch size, what each weigh hopper can hold of the recipe, discharge-hopper size).
* Materials sharing one weigh hopper (sand + gravel) are weighed **sequentially**; a hopper cannot be refilled until it
  has dumped into the mixer (no double buffering) — adding a parallel hopper unit models double buffering.
* A line fills at the **slowest rate** along source → conveyor → hopper; equipment shared by several lines is serialised.
* Every duration gets an optional triangular ±variability. Storage refills are not modelled; the report only gives how
  long a full store lasts at the simulated rate.
* *Add a parallel unit* assumes the new unit is connected like the existing one (draw it yourself in Visio).

## Tests

`python -m unittest discover -s tests -v` — covers type inference, rule detection and step-by-step repair, the DES
against hand-calculated cycle times, hopper-limited batch size, parallel mixers, `.vsdx` read round-trip, AI reply
parsing/validation (mock), and the equipment-data validation.

## Layout

```
main.py              GUI entry / `cli` entry          bpsim/catalog.py   equipment types, units, defaults
bpsim/model.py       plant graph + recipe             bpsim/rules.py     completeness rules + fixes
bpsim/fixes.py       applies correction actions       bpsim/verify.py    rules+AI, correction session
bpsim/ai.py          free-API client + validation     bpsim/rates.py     equipment data logic
bpsim/des.py         event kernel                     bpsim/simulate.py  plant DES
bpsim/analysis.py    bottlenecks + what-if plan       bpsim/ui.py        tkinter GUI + dialogs
bpsim/visio_reader.py / visio_writer.py   COM + .vsdx   visio/BatchingPlantSim.bas   launcher macro
samples/             complete & defective plants (JSON, .vsdx), generators
```
