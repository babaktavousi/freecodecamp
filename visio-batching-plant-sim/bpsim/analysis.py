"""Bottleneck identification and simulation-driven debottlenecking plan."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from . import catalog as C
from .model import PlantModel
from .simulate import SimResult, SimSettings, run_simulation


@dataclass
class Mod:
    eid: str
    param: str
    old: float
    new: float
    text: str
    cost: int


@dataclass
class PlanStep:
    mod: Mod
    throughput_before: float
    throughput_after: float
    bottleneck_after: str | None

    @property
    def gain_pct(self) -> float:
        return 100.0 * (self.throughput_after / self.throughput_before - 1.0) if self.throughput_before else 0.0


@dataclass
class Analysis:
    result: SimResult
    settings: SimSettings
    ranking: list = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    final: SimResult | None = None
    final_model: PlantModel | None = None
    target: float | None = None
    design_m3h: float = 0.0


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.2f}".rstrip("0").rstrip(".")


LIMITS = {"rate": (None, 2.5), "capacity": (None, 2.0), "time": (0.7, None)}   # vs. the original value


def candidates(m: PlantModel, eid: str, base: PlantModel | None = None) -> list[Mod]:
    """Realistic single changes for one equipment (bounded relative to the as-built values in `base`)."""
    e = m.equipment[eid]
    out: list[Mod] = []
    if not e.spec:
        return out
    for p in e.spec.params:
        cur = e.p(p.key, p.default)
        orig = (base or m).equipment[eid].p(p.key, p.default)
        if p.key == "units" and cur >= orig + 1:
            continue
        if p.key == "units":
            out.append(Mod(eid, "units", cur, cur + 1, f"Add one more parallel {e.spec.label.lower()} to {eid} "
                           f"({int(cur)} → {int(cur) + 1} units, connected like the existing one)", 3))
        elif p.kind == "rate":
            for f, c in ((1.25, 1), (1.5, 2), (2.0, 3)):
                out.append(Mod(eid, p.key, cur, round(cur * f, 3),
                               f"Increase {p.label.lower()} of {eid} from {_fmt(cur)} to {_fmt(cur * f)} {p.unit} "
                               f"(+{int((f - 1) * 100)} %)", c))
        elif p.kind == "capacity" and p.better == "high":
            for f, c in ((1.25, 1), (1.5, 2)):
                out.append(Mod(eid, p.key, cur, round(cur * f, 3),
                               f"Increase {p.label.lower()} of {eid} from {_fmt(cur)} to {_fmt(cur * f)} {p.unit} "
                               f"(+{int((f - 1) * 100)} %)", c))
        elif p.kind == "time" and p.better == "low":
            for f, c in ((0.8, 1), (0.6, 2)):
                out.append(Mod(eid, p.key, cur, round(cur * f, 3),
                               f"Reduce {p.label.lower()} of {eid} from {_fmt(cur)} to {_fmt(cur * f)} {p.unit} "
                               f"(-{int((1 - f) * 100)} %)", c))
    lo_hi = lambda mod, p: LIMITS.get(p.kind, (None, None))
    keep = []
    for mod in out:
        p = next(q for q in e.spec.params if q.key == mod.param)
        orig = (base or m).equipment[eid].p(p.key, p.default)
        lo, hi = LIMITS.get(p.kind, (None, None)) if p.key != "units" else (None, None)
        if lo is not None and mod.new < lo * orig - 1e-9:
            continue
        if hi is not None and mod.new > hi * orig + 1e-9:
            continue
        keep.append(mod)
    return keep


def apply_mod(m: PlantModel, mod: Mod) -> None:
    m.equipment[mod.eid].params[mod.param] = mod.new


def _quick(st: SimSettings) -> SimSettings:
    return replace(st, variability=0.0, replications=1, duration_h=min(st.duration_h, 3.0),
                   warmup_h=min(st.warmup_h, 0.25))


def debottleneck(model: PlantModel, st: SimSettings, target: float | None = None,
                 max_steps: int = 10, min_gain: float = 0.015, progress=None) -> list[PlanStep]:
    cur = model.copy()
    q = _quick(st)
    thr = run_simulation(cur, q).throughput_m3h
    steps: list[PlanStep] = []
    for k in range(max_steps):
        if target and thr >= target:
            break
        trials = []
        for eid, e in cur.equipment.items():
            if not e.spec or e.spec.role in ("control", "aux"):
                continue
            for mod in candidates(cur, eid, model):
                t = cur.copy()
                apply_mod(t, mod)
                r = run_simulation(t, q)
                if not r.error and not r.deadlock:
                    trials.append((r.throughput_m3h, mod, r))
        if not trials:
            break
        best = max(t[0] for t in trials)
        if best / thr - 1.0 < min_gain:
            break
        eligible = [t for t in trials if t[0] - thr >= 0.9 * (best - thr)]
        thr2, mod, r = min(eligible, key=lambda t: (t[1].cost, -t[0]))
        apply_mod(cur, mod)
        steps.append(PlanStep(mod, thr, thr2, r.bottleneck))
        if progress:
            progress(f"step {k + 1}: {mod.text}")
        thr = thr2
    return steps


def analyse(model: PlantModel, st: SimSettings, target: float | None = None, progress=None) -> Analysis:
    res = run_simulation(model, st)
    a = Analysis(res, st, target=target)
    if res.error:
        return a
    a.ranking = sorted(res.equipment.values(), key=lambda s: (-s.util, -s.own_util))
    a.design_m3h = _design_capacity(model)
    a.findings = findings(model, res, a.design_m3h, target)
    a.plan = debottleneck(model, st, target, progress=progress)
    fm = model.copy()
    for s in a.plan:
        apply_mod(fm, s.mod)
    a.final_model = fm
    a.final = run_simulation(fm, st) if a.plan else res
    return a


def _design_capacity(m: PlantModel) -> float:
    """Nameplate mixer capacity (charge + mix + discharge cycle, nothing else limiting)."""
    tot = 0.0
    for e in m.of_type("mixer"):
        dumps = [h.p("dump_time_s") for h in m.of_type("weigh_hopper")] or [0.0]
        cyc = max(dumps) + e.p("mix_time_s") + e.p("discharge_time_s")
        tot += e.p("units", 1) * e.p("capacity") * 3600 / cyc if cyc else 0.0
    return tot


def findings(m: PlantModel, res: SimResult, design: float, target: float | None) -> list[str]:
    out: list[str] = []
    bn = res.equipment[res.bottleneck]
    out.append(f"Plant capacity is {res.throughput_m3h:.1f} m³/h ({design and 100 * res.throughput_m3h / design:.0f} % of the "
               f"{design:.1f} m³/h nameplate capacity of the mixer(s)).")
    out.append(f"Bottleneck: {bn.id} ({C.TYPES[bn.type].label}) is working {100 * bn.util:.0f} % of the time"
               f" - no other single item limits the plant more.")
    near = [s.id for s in res.equipment.values() if s.id != bn.id and s.util >= bn.util - 0.1]
    if near:
        out.append(f"Close to the limit as well (within 10 %): {', '.join(near)} - upgrading only the bottleneck "
                   f"will quickly move the constraint here.")
    if target:
        gap = target - res.throughput_m3h
        out.append(f"Target {target:.0f} m³/h is {'MET' if gap <= 0 else f'NOT met (short by {gap:.1f} m³/h)'}.")
    if res.notes:
        out.append("Note: " + res.notes[0] + ("." if not res.notes[0].endswith(".") else ""))
    for s in res.equipment.values():
        e = m.equipment[s.id]
        if s.autonomy_h is not None and s.autonomy_h < 2.0:
            out.append(f"{s.id}: a full store ({e.p('capacity'):g} t) lasts only {s.autonomy_h:.1f} h at this "
                       f"production rate - plan refills during production or a bigger store.")
    low = [s.id for s in res.equipment.values() if s.util < 0.3 and s.own_util < 0.25
           and C.TYPES[s.type].role in ("source", "transfer")]
    if low:
        out.append(f"Lightly loaded (<30 %): {', '.join(low)} - adequately sized; no upgrade needed.")
    idle_mix = [s.id for s in res.equipment.values() if s.type == "mixer" and s.util < 0.7]
    if idle_mix:
        out.append(f"Mixer(s) {', '.join(idle_mix)} idle >30 % of the time: the plant is starved by the feed/weighing "
                   f"line or blocked by loading, not by the mixer.")
    return out


def report_text(model: PlantModel, a: Analysis) -> str:
    r = a.result
    L = [f"BATCHING PLANT SIMULATION REPORT - {model.name}", "=" * 72]
    if r.error:
        return "\n".join(L + [f"Simulation could not run: {r.error}"])
    L += [f"Simulated {a.settings.duration_h:g} h (warm-up {a.settings.warmup_h:g} h), WIP limit {a.settings.wip}, "
          f"{a.settings.replications if a.settings.variability else 1} replication(s), ±{100 * a.settings.variability:.0f} % duration variability",
          "", f"OVERALL PRODUCTION CAPACITY : {r.throughput_m3h:.1f} m³/h" +
          (f"  (95 % CI ± {r.ci95:.1f})" if r.ci95 else ""),
          f"Batches per hour            : {r.batches_per_h:.1f}  (batch {r.batch_m3:.2f} m³, one every {r.cycle_s:.0f} s)",
          f"Bottleneck                  : {r.bottleneck}", ""]
    if r.deadlock:
        L.append("WARNING: the simulation stalled (deadlock) - check connections.\n")
    L += ["EQUIPMENT LOAD (sorted)", f"{'ID':9}{'Type':28}{'Units':>5}{'Busy %':>8}{'Blocked %':>10}{'Plant limit m3/h':>18}"]
    for s in a.ranking:
        lim = "-" if s.limit_m3h == float("inf") else f"{s.limit_m3h:.1f}"
        L.append(f"{s.id:9}{C.TYPES[s.type].label[:27]:28}{s.units:>5}{100 * s.util:>8.0f}{100 * s.blocked:>10.0f}{lim:>18}")
    L += ["", "FINDINGS"] + [f" - {f}" for f in a.findings]
    L += ["", "WHAT TO MODIFY (in order; each step re-simulated)"]
    if not a.plan:
        L.append(" No single modification gives a worthwhile gain - the system is balanced at its current capacity.")
    for i, s in enumerate(a.plan, 1):
        L.append(f" {i}. {s.mod.text}\n    -> {s.throughput_before:.1f} → {s.throughput_after:.1f} m³/h "
                 f"(+{s.gain_pct:.1f} %), next bottleneck: {s.bottleneck_after}")
    if a.plan and a.final:
        L += ["", f"CAPACITY AFTER ALL MODIFICATIONS : {a.final.throughput_m3h:.1f} m³/h "
              f"(+{100 * (a.final.throughput_m3h / r.throughput_m3h - 1):.0f} %), limiting item: {a.final.bottleneck}"]
        if a.target:
            L.append(f"Target {a.target:.0f} m³/h: {'reached' if a.final.throughput_m3h >= a.target else 'not reached by single-item upgrades - consider a second line'}")
    return "\n".join(L)


def report_dict(model: PlantModel, a: Analysis) -> dict:
    r = a.result
    return {"plant": model.name, "throughput_m3h": r.throughput_m3h, "ci95": r.ci95, "batches_per_h": r.batches_per_h,
            "batch_m3": r.batch_m3, "bottleneck": r.bottleneck, "findings": a.findings,
            "equipment": [{"id": s.id, "type": s.type, "util": s.util, "blocked": s.blocked,
                           "limit_m3h": None if s.limit_m3h == float("inf") else s.limit_m3h} for s in a.ranking],
            "plan": [{"change": s.mod.text, "before": s.throughput_before, "after": s.throughput_after} for s in a.plan],
            "final_throughput_m3h": a.final.throughput_m3h if a.final else r.throughput_m3h}
