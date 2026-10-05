"""Discrete-event simulation of the batching plant.

Batches (entities) are released as fast as the plant can take them (work-in-process limited, i.e. unlimited
truck demand). Each batch:
  1. fills the weigh hoppers: source -> (conveyor/screw/pump) -> hopper are seized together and held for
     mass / slowest-rate seconds (a hopper that is still occupied by the previous batch blocks the line);
  2. waits for the mixer, dumps all hoppers into it (dump time / post-hopper conveyor rate);
  3. mixes, then discharges into the discharge hopper / truck (a slow truck blocks the mixer if there is no
     buffer hopper);
Every equipment is a resource with `units` parallel servers. Optional triangular randomness on all durations.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field

from . import catalog as C
from .des import Env, Pool, Resource
from .model import PlantModel


@dataclass
class SimSettings:
    duration_h: float = 8.0
    warmup_h: float = 0.5
    wip: int = 4                 # max batches in the plant at once
    replications: int = 5
    variability: float = 0.05    # +/- triangular spread on every duration (0 = deterministic)
    seed: int = 1
    target_batch_m3: float | None = None


@dataclass
class EquipStat:
    id: str
    type: str
    units: int
    util: float = 0.0            # fraction of time actively working
    blocked: float = 0.0         # fraction of time seized but waiting
    own_util: float = 0.0        # load at the equipment's own rated speed
    limit_m3h: float = 0.0       # plant output if this were the only constraint
    consumed_t: float = 0.0
    autonomy_h: float | None = None


@dataclass
class SimResult:
    throughput_m3h: float = 0.0
    throughput_std: float = 0.0
    ci95: float = 0.0
    batches_per_h: float = 0.0
    batch_m3: float = 0.0
    cycle_s: float = 0.0
    equipment: dict[str, EquipStat] = field(default_factory=dict)
    bottleneck: str | None = None
    notes: list[str] = field(default_factory=list)
    error: str = ""
    deadlock: bool = False


class PlantSim:
    def __init__(self, model: PlantModel, st: SimSettings, seed: int) -> None:
        self.m, self.st = model, st
        self.rng = random.Random(seed)
        self.env = Env()
        self.nodes = [e for e in model.equipment.values() if e.spec and e.spec.role not in ("control", "aux")]
        self.res = {e.id: Resource(self.env, e.id, int(e.p("units", 1))) for e in self.nodes}
        self.recipe = model.effective_recipe()
        self.assigned: dict[str, float] = {e.id: 0.0 for e in self.nodes}
        self.mixer_load: dict[str, int] = {}
        self.wip = Pool(self.env, max(int(st.wip), 1))
        self.bid = 0
        self.done_vol = 0.0
        self.done_batches = 0
        self.cycles: list[float] = []
        self.limiters: dict[str, int] = {}
        self.consumed: dict[str, float] = {}
        self.vols: list[float] = []
        self._build_paths()

    # ------------------------------------------------------------------ static structure
    def role(self, n: str) -> str:
        return self.m.equipment[n].spec.role

    def rate(self, n: str) -> float | None:
        e = self.m.equipment[n]
        return e.p("rate") if e.spec.role in ("source", "transfer", "buffer", "sink") else None

    def _paths_from(self, start: str, target: str | None, stop_roles: set[str]) -> list[list[str]]:
        out: list[list[str]] = []
        def dfs(n: str, path: list[str]) -> None:
            if len(out) > 60:
                return
            for s in self.m.succ(n):
                role = self.role(s) if self.m.equipment[s].spec else None
                if role is None:
                    continue
                if target is not None and s == target:
                    out.append(path[:])
                elif role in stop_roles and target is None:
                    out.append(path + [s])
                elif role in ("mixer", "sink") or s in path or (target is None and role == "buffer"):
                    continue
                else:
                    dfs(s, path + [s])
        dfs(start, [start])
        return out

    def _build_paths(self) -> None:
        m = self.m
        self.mixers = [e.id for e in m.of_type("mixer")]
        sources: dict[str, list[str]] = {}
        for e in m.of_type("aggregate_bin", "cement_silo", "water_tank", "admixture_tank"):
            sources.setdefault(e.material, []).append(e.id)
        self.paths: dict[str, dict[str, list[list[str]]]] = {}
        for mx in self.mixers:
            per_mat = {}
            for mat in self.recipe:
                ps = []
                for s in sources.get(mat, []):
                    ps += self._paths_from(s, mx, set())
                if ps:
                    per_mat[mat] = ps
            if set(per_mat) == set(self.recipe):
                self.paths[mx] = per_mat
        self.out_paths: dict[str, list[list[str]]] = {}
        for mx in self.mixers:
            ps = []
            def dfs(n, path):
                for s in m.succ(n):
                    if s in path or not m.equipment[s].spec:
                        continue
                    r = self.role(s)
                    if r == "sink":
                        ps.append(path + [s])
                    elif r == "buffer":
                        dfs(s, path + [s])
            dfs(mx, [])
            if ps:
                self.out_paths[mx] = ps

    # ------------------------------------------------------------------ planning of one batch
    def vf(self) -> float:
        v = self.st.variability
        return self.rng.triangular(1 - v, 1 + v, 1.0) if v > 0 else 1.0

    def _path_cost(self, path: list[str], t_per_node: dict[str, float]) -> float:
        return max((self.assigned[n] + t_per_node.get(n, 0.0)) / self.res[n].units for n in path)

    def plan_batch(self) -> dict:
        m, bid = self.m, self.bid
        self.bid += 1
        feasible = [x for x in self.mixers if x in self.paths and x in self.out_paths]
        if not feasible:
            raise RuntimeError("no mixer has a complete supply path for every material and a path to a loadout")
        mx = min(feasible, key=lambda x: (self.mixer_load.get(x, 0) / self.res[x].units, x))
        self.mixer_load[mx] = self.mixer_load.get(mx, 0) + 1
        mix_e = m.equipment[mx]
        V = mix_e.p("capacity", 1.0)
        limiter = f"mixer {mx} capacity"
        if self.st.target_batch_m3 and self.st.target_batch_m3 < V:
            V, limiter = self.st.target_batch_m3, "target batch size setting"
        # choose a supply path per material
        order = list(self.recipe)
        chosen: dict[str, list[str]] = {}
        for mat in order:
            def work(path):
                return {n: self.recipe[mat] * V / 1000 / self.rate(n) * 3600 for n in path if self.rate(n)}
            chosen[mat] = min(self.paths[mx][mat], key=lambda p: self._path_cost(p, work(p)))
        jobs: dict[str, dict] = {}
        for mat in order:
            path = chosen[mat]
            idx = max((i for i, n in enumerate(path) if self.role(n) == "hopper"), default=len(path) - 1)
            H = path[idx]
            j = jobs.setdefault(H, {"H": H, "lines": [], "post": path[idx + 1:]})
            j["lines"].append({"mat": mat, "pre": path[:idx + 1], "kg_m3": self.recipe[mat]})
            if len(path[idx + 1:]) > len(j["post"]):
                j["post"] = path[idx + 1:]
        # batch volume limited by weigh hopper capacities
        for j in jobs.values():
            h = m.equipment[j["H"]]
            if h.spec.role == "hopper":
                vmax = h.p("capacity") / sum(l["kg_m3"] for l in j["lines"])
                if vmax < V:
                    V, limiter = vmax, f"weigh hopper {h.id} capacity"
        outs = self.out_paths[mx]
        def owork(p):
            return {n: V / self.rate(n) * 3600 for n in p if self.rate(n)}
        out = min(outs, key=lambda p: self._path_cost(p, owork(p)))
        for n in out:
            if self.role(n) == "buffer" and m.equipment[n].p("capacity") < V:
                V, limiter = m.equipment[n].p("capacity"), f"discharge hopper {n} capacity"
        self.limiters[limiter] = self.limiters.get(limiter, 0) + 1
        # masses, work bookkeeping, token registration (order = order of requests)
        job_list = list(jobs.values())
        for ji, j in enumerate(job_list):
            j["mass"] = 0.0
            for li, line in enumerate(j["lines"]):
                line["mass"] = line["kg_m3"] * V / 1000.0
                j["mass"] += line["mass"]
                line["tok"] = {}
                for n in line["pre"]:
                    if n == j["H"] and li > 0:
                        continue
                    tok = ("f", bid, ji, li, n)
                    line["tok"][n] = tok
                    self.res[n].register(tok)
                    if self.rate(n):
                        self.assigned[n] += line["mass"] / self.rate(n) * 3600
                src = line["pre"][0]
                self.consumed[src] = self.consumed.get(src, 0.0) + line["mass"]
            j["htok"] = j["lines"][0]["tok"][j["H"]]
        mtok = ("m", bid)
        self.res[mx].register(mtok)
        for ji, j in enumerate(job_list):
            j["dtok"] = {}
            for n in j["post"]:
                tok = ("d", bid, ji, n)
                j["dtok"][n] = tok
                self.res[n].register(tok)
                if self.rate(n):
                    self.assigned[n] += j["mass"] / self.rate(n) * 3600
        segs, cur = [], []
        for n in out:
            cur.append(n)
            if self.role(n) == "buffer":
                segs.append(cur)
                cur = []
        if cur:
            segs.append(cur)
        otok = {}
        for si, seg in enumerate(segs):
            for n in seg:
                otok[(si, n)] = ("o", bid, si, n)
                self.res[n].register(otok[(si, n)])
                if self.rate(n):
                    self.assigned[n] += V / self.rate(n) * 3600
        return {"id": bid, "V": V, "mixer": mx, "mtok": mtok, "jobs": job_list, "segs": segs, "otok": otok}

    # ------------------------------------------------------------------ processes
    def _job_fill(self, b: dict, job: dict):
        H = job["H"]
        for li, line in enumerate(job["lines"]):
            got = []
            for n in line["pre"]:
                if n == H and li > 0:
                    continue
                yield self.res[n].request(line["tok"][n])
                got.append(n)
            rates = [self.rate(n) for n in line["pre"] if self.rate(n)]
            dur = line["mass"] / min(rates) * 3600 if rates else 0.0
            he = self.m.equipment[H]
            settle = he.p("settle_time_s") if he.spec.role == "hopper" else 0.0
            dur = (dur + settle) * self.vf()
            for n in line["pre"]:
                r = self.res[n]
                r.active += dur
                if self.rate(n):
                    r.own += line["mass"] / self.rate(n) * 3600
            if he.spec.role == "hopper":
                self.res[H].own += settle
            yield self.env.timeout(dur)
            for n in got:
                if n != H:
                    self.res[n].release(line["tok"][n])

    def _job_dump(self, b: dict, job: dict):
        H = job["H"]
        got = []
        for n in job["post"]:
            yield self.res[n].request(job["dtok"][n])
            got.append(n)
        he = self.m.equipment[H]
        base = he.p("dump_time_s") if he.spec.role == "hopper" else 0.0
        rates = [self.rate(n) for n in job["post"] if self.rate(n)]
        dur = max(base, job["mass"] / min(rates) * 3600 if rates else 0.0) * self.vf()
        self.res[H].active += dur
        self.res[H].own += base
        for n in job["post"]:
            self.res[n].active += dur
            self.res[n].own += job["mass"] / self.rate(n) * 3600 if self.rate(n) else 0.0
        yield self.env.timeout(dur)
        for n in got:
            self.res[n].release(job["dtok"][n])
        self.res[H].release(job["htok"])

    def _run_batch(self, b: dict):
        env, mx = self.env, b["mixer"]
        t_start = env.now
        for p in [env.process(self._job_fill(b, j)) for j in b["jobs"]]:
            yield p.finished
        yield self.res[mx].request(b["mtok"])
        t0 = env.now
        for p in [env.process(self._job_dump(b, j)) for j in b["jobs"]]:
            yield p.finished
        me = self.m.equipment[mx]
        mix = me.p("mix_time_s") * self.vf()
        yield env.timeout(mix)
        charge_mix = env.now - t0
        r = self.res[mx]
        r.active += charge_mix
        r.own += charge_mix
        prev = (mx, b["mtok"])
        V = b["V"]
        for si, seg in enumerate(b["segs"]):
            got = []
            for n in seg:
                yield self.res[n].request(b["otok"][(si, n)])
                got.append(n)
            # a buffer hopper at the end of the segment only receives; its outlet rate limits the next segment
            flow = [n for k, n in enumerate(seg) if not (k == len(seg) - 1 and self.role(n) == "buffer")]
            if si > 0:
                flow = [prev[0]] + flow
            rates = [self.rate(n) for n in flow if self.rate(n)]
            dur = V / min(rates) * 3600 if rates else 0.0
            if si == 0:
                dur = max(dur, me.p("discharge_time_s"))
            dur *= self.vf()
            for n in set(seg) | ({prev[0]} if si > 0 else set()):
                self.res[n].active += dur
            for n in flow:
                self.res[n].own += V / self.rate(n) * 3600 if self.rate(n) else 0.0
            if si == 0:
                r.active += dur
                r.own += dur
            yield env.timeout(dur)
            self.res[prev[0]].release(prev[1])
            for n in got[:-1]:
                self.res[n].release(b["otok"][(si, n)])
            prev = (got[-1], b["otok"][(si, got[-1])])
        self.res[prev[0]].release(prev[1])
        self.done_vol += V
        self.done_batches += 1
        self.vols.append(V)
        self.cycles.append(env.now - t_start)
        self.wip.release()

    def _producer(self):
        while True:
            yield self.wip.request()
            self.env.process(self._run_batch(self.plan_batch()))

    def _reset(self) -> None:
        for r in self.res.values():
            r.reset(self.env.now)
        self.done_vol, self.done_batches = 0.0, 0
        self.cycles, self.vols, self.consumed = [], [], {}
        self.mixer_done_marks = 0

    # ------------------------------------------------------------------ run
    def run(self) -> SimResult:
        st, env = self.st, self.env
        end, warm = st.duration_h * 3600.0, min(st.warmup_h, st.duration_h * 0.5) * 3600.0
        env.process(self._producer())
        env.schedule(warm, self._reset)
        env.run(end)
        res = SimResult()
        if env.last_event < end - 1e-6 and self.done_batches < 3:
            res.deadlock = True
        for r in self.res.values():
            r.close(end)
        T = max(end - warm, 1.0)
        res.throughput_m3h = self.done_vol / (T / 3600)
        res.batches_per_h = self.done_batches / (T / 3600)
        res.batch_m3 = statistics.mean(self.vols) if self.vols else 0.0
        res.cycle_s = T / self.done_batches if self.done_batches else 0.0
        for e in self.nodes:
            r = self.res[e.id]
            cap = r.units * T
            s = EquipStat(e.id, e.type, r.units, min(r.active / cap, 1.0),
                          max(r.held - r.active, 0.0) / cap, min(r.own / cap, 1.0))
            s.limit_m3h = res.throughput_m3h / s.util if s.util > 1e-6 else float("inf")
            s.consumed_t = self.consumed.get(e.id, 0.0)
            if e.spec.role == "source" and s.consumed_t > 0:
                s.autonomy_h = e.p("capacity") / (s.consumed_t / (T / 3600))
            res.equipment[e.id] = s
        res.notes = [f"batch size {k} limits the batch" for k, v in
                     sorted(self.limiters.items(), key=lambda kv: -kv[1])[:1]]
        return res


def _pick_bottleneck(eqs: dict[str, EquipStat]) -> str | None:
    if not eqs:
        return None
    top = max(s.util for s in eqs.values())
    cands = [s for s in eqs.values() if s.util >= top - 0.02]
    return max(cands, key=lambda s: (s.own_util, s.util)).id


def run_simulation(model: PlantModel, st: SimSettings | None = None) -> SimResult:
    st = st or SimSettings()
    runs = []
    n = max(int(st.replications), 1) if st.variability > 0 else 1
    try:
        for i in range(n):
            runs.append(PlantSim(model, st, st.seed + i).run())
    except RuntimeError as exc:
        return SimResult(error=str(exc))
    out = runs[0]
    thr = [r.throughput_m3h for r in runs]
    out.throughput_m3h = statistics.mean(thr)
    out.throughput_std = statistics.stdev(thr) if len(thr) > 1 else 0.0
    out.ci95 = 1.96 * out.throughput_std / math.sqrt(len(thr)) if len(thr) > 1 else 0.0
    out.batches_per_h = statistics.mean(r.batches_per_h for r in runs)
    out.cycle_s = statistics.mean(r.cycle_s for r in runs)
    out.batch_m3 = statistics.mean(r.batch_m3 for r in runs)
    out.deadlock = any(r.deadlock for r in runs)
    for k, s in out.equipment.items():
        s.util = statistics.mean(r.equipment[k].util for r in runs)
        s.blocked = statistics.mean(r.equipment[k].blocked for r in runs)
        s.own_util = statistics.mean(r.equipment[k].own_util for r in runs)
        s.consumed_t = statistics.mean(r.equipment[k].consumed_t for r in runs)
        s.limit_m3h = out.throughput_m3h / s.util if s.util > 1e-6 else float("inf")
        if s.consumed_t > 0 and runs[0].equipment[k].autonomy_h is not None:
            s.autonomy_h = statistics.mean(r.equipment[k].autonomy_h for r in runs if r.equipment[k].autonomy_h)
    out.bottleneck = _pick_bottleneck(out.equipment)
    return out
