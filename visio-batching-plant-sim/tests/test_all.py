import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bpsim import catalog as C, rates                                  # noqa: E402
from bpsim.ai import AIClient, AIConfig, _extract_json, validate_ai_steps   # noqa: E402
from bpsim.analysis import analyse, candidates                         # noqa: E402
from bpsim.model import PlantModel                                      # noqa: E402
from bpsim.rules import is_complete, verify_rules                       # noqa: E402
from bpsim.simulate import SimSettings, run_simulation                  # noqa: E402
from bpsim.verify import CorrectionSession, run_verification            # noqa: E402
from bpsim.visio_reader import read_vsdx                                # noqa: E402

S = os.path.join(os.path.dirname(__file__), "..", "samples")
load = lambda n: PlantModel.load(os.path.join(S, n))


class Catalog(unittest.TestCase):
    def test_inference(self):
        self.assertEqual(C.infer_type(None, "CSL-07"), "cement_silo")
        self.assertEqual(C.infer_type(None, None, "Water tank"), "water_tank")
        self.assertEqual(C.infer_type(None, None, "Aggregate weigh hopper"), "weigh_hopper")
        self.assertEqual(C.infer_type("Mixer", "XX"), "mixer")
        self.assertEqual(C.infer_type(None, None, "Discharge hopper"), "discharge_hopper")
        self.assertEqual(C.infer_type(None, None, "Admixture tank"), "admixture_tank")
        self.assertIsNone(C.infer_type(None, None, "Plant layout title"))


class Verification(unittest.TestCase):
    def test_complete_model_passes(self):
        self.assertTrue(is_complete(verify_rules(load("sample_plant_complete.json"))))

    def test_defective_detected_then_fixed_step_by_step(self):
        m = load("sample_plant_defective.json")
        errs = [i.code for i in verify_rules(m) if i.severity == "error"]
        for code in ("INVALID_CONNECTION", "MISSING_LOADOUT", "MISSING_CONTROLLER", "MIXER_NO_WATER", "NO_WEIGH_HOPPER"):
            self.assertIn(code, errs)
        sess = CorrectionSession(m)
        steps = 0
        while sess.plan() and steps < 60:
            ok, msg = sess.apply(sess.plan()[0])
            self.assertTrue(ok, msg)
            steps += 1
        self.assertGreater(steps, 5)
        rep = run_verification(m, None)               # final re-verification
        self.assertTrue(rep.complete, [i.message for i in rep.issues])

    def test_cycle_and_reverse(self):
        m = load("sample_plant_complete.json")
        m.add_connection("MIX-01", "WGH-01")
        self.assertIn("INVALID_CONNECTION", [i.code for i in verify_rules(m)])


class Simulation(unittest.TestCase):
    def test_matches_hand_calculation(self):
        r = run_simulation(load("sample_plant_complete.json"), SimSettings(variability=0))
        self.assertFalse(r.deadlock)
        # aggregate hopper cycle: 1.6t/120 + 2.0t/150 per m3.. = 48 s + 48 s + 2x5 s settle + 8 s dump = 114 s per 2 m3
        self.assertAlmostEqual(r.throughput_m3h, 2.0 * 3600 / 114, delta=1.0)
        self.assertEqual(r.bottleneck, "WGH-01")

    def test_mixer_bound_when_feed_is_fast(self):
        m = load("sample_plant_complete.json")
        for i in ("AGB-01", "AGB-02", "CNV-01"):
            m.equipment[i].params["rate"] = 1000
        for i in ("WGH-01", "WGH-02", "WGH-03", "WGH-04"):
            m.equipment[i].params.update(settle_time_s=1, dump_time_s=2)
        m.equipment["TRK-01"].params["rate"] = 500
        m.equipment["DCH-01"].params["rate"] = 500
        r = run_simulation(m, SimSettings(variability=0))
        self.assertEqual(r.bottleneck, "MIX-01")
        self.assertAlmostEqual(r.throughput_m3h, 2.0 * 3600 / (2 + 45 + 20), delta=2.0)

    def test_second_mixer_scales_when_mixer_bound(self):
        m = load("sample_plant_complete.json")
        for i in ("AGB-01", "AGB-02", "CNV-01"):
            m.equipment[i].params["rate"] = 2000
        for i in ("TRK-01", "DCH-01"):
            m.equipment[i].params["rate"] = 1000
        for i in ("WGH-01", "WGH-02", "WGH-03", "WGH-04"):
            m.equipment[i].params.update(settle_time_s=1, dump_time_s=2, units=2)
        for i in ("CSL-01", "SCW-01"):
            m.equipment[i].params["rate"] = 500
        m.equipment["MIX-01"].params["units"] = 2
        r = run_simulation(m, SimSettings(variability=0))
        self.assertGreater(r.throughput_m3h, 80)

    def test_deterministic_and_seeded(self):
        m = load("sample_plant_complete.json")
        a = run_simulation(m, SimSettings(replications=2)).throughput_m3h
        b = run_simulation(m, SimSettings(replications=2)).throughput_m3h
        self.assertEqual(a, b)

    def test_incomplete_model_reports_error(self):
        m = load("sample_plant_complete.json")
        m.remove_connection("WGH-03", "MIX-01")
        self.assertTrue(run_simulation(m, SimSettings()).error)

    def test_hopper_capacity_limits_batch(self):
        m = load("sample_plant_complete.json")
        m.equipment["WGH-01"].params["capacity"] = 1800     # exactly 1 m3 of aggregate (800 sand + 1000 gravel)
        r = run_simulation(m, SimSettings(variability=0))
        self.assertAlmostEqual(r.batch_m3, 1.0, delta=0.01)


class Analysis(unittest.TestCase):
    def test_plan_improves_capacity(self):
        m = load("sample_plant_complete.json")
        a = analyse(m, SimSettings(replications=1, variability=0))
        self.assertTrue(a.plan)
        self.assertGreater(a.final.throughput_m3h, a.result.throughput_m3h * 1.1)
        self.assertTrue(all(s.throughput_after > s.throughput_before for s in a.plan))

    def test_candidates_bounded(self):
        m = load("sample_plant_complete.json")
        c = candidates(m, "MIX-01", m)
        self.assertTrue(all(x.new >= 0.7 * x.old or x.param == "units" or x.new > x.old for x in c))


class VisioReader(unittest.TestCase):
    def test_vsdx_roundtrip(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            for n in ("complete", "defective"):
                out = os.path.join(d, n + ".vsdx")
                subprocess.check_call([sys.executable, os.path.join(S, "make_sample_vsdx.py"),
                                       os.path.join(S, f"sample_plant_{n}.json"), out], stdout=subprocess.DEVNULL)
                a, b = read_vsdx(out), load(f"sample_plant_{n}.json")
                self.assertEqual({(c.src, c.dst, c.kind) for c in a.connections}, {(c.src, c.dst, c.kind) for c in b.connections})
                self.assertEqual({e.id: e.type for e in a.equipment.values()}, {e.id: e.type for e in b.equipment.values()})


class AI(unittest.TestCase):
    def test_json_extraction(self):
        self.assertEqual(_extract_json('```json\n{"complete": true}\n```')["complete"], True)
        self.assertEqual(_extract_json('Sure! {"a": 1} hope it helps')["a"], 1)

    def test_ai_steps_are_validated(self):
        m = load("sample_plant_defective.json")
        steps = [
            {"title": "good", "why": "", "actions": [{"op": "add_equipment", "type": "controller", "id": "CTL-09"}]},
            {"title": "hallucinated id", "actions": [{"op": "add_connection", "from": "NOPE-1", "to": "MIX-01"}]},
            {"title": "bad type", "actions": [{"op": "add_equipment", "type": "teleporter"}]},
            {"title": "bad op", "actions": [{"op": "format_disk"}]},
        ]
        good = validate_ai_steps(m, steps)
        self.assertEqual([g.title for g in good], ["good"])

    def test_mock_ai_flow(self):
        class Mock(AIClient):
            def chat(self, system, user):
                return json.dumps({"complete": False, "summary": "no controller",
                                   "issues": [{"severity": "error", "message": "no PLC", "equipment": []}],
                                   "steps": [{"title": "Add PLC", "why": "control", "actions": [
                                       {"op": "add_equipment", "type": "controller", "id": "CTL-01"}]}]})
        m = load("sample_plant_complete.json")
        m.remove_equipment("CTL-01")
        rep = run_verification(m, Mock(AIConfig(provider="custom", base_url="x", model="x")))
        self.assertTrue(rep.ai.available and rep.ai_blocking)
        self.assertFalse(rep.complete)

    def test_ai_unreachable_degrades_to_rules(self):
        class Dead(AIClient):
            def chat(self, system, user):
                raise RuntimeError("AI service unreachable")
        rep = run_verification(load("sample_plant_complete.json"), Dead(AIConfig(provider="custom", base_url="x", model="x")))
        self.assertTrue(rep.complete and not rep.ai_checked)


class Rates(unittest.TestCase):
    def test_flow(self):
        m = load("sample_plant_defective.json")
        self.assertTrue(rates.needs_data(m))
        e = m.equipment["MIX-01"]
        self.assertTrue(rates.validate(e, {"capacity": "x", "mix_time_s": 45, "discharge_time_s": 20, "units": 1}))
        self.assertTrue(rates.validate(e, {"capacity": 2, "mix_time_s": 0, "discharge_time_s": 20, "units": 1}))
        self.assertFalse(rates.validate(e, {"capacity": 2, "mix_time_s": 45, "discharge_time_s": 20, "units": 1}))
        rates.apply_defaults(m)
        self.assertTrue(rates.all_final(m))


if __name__ == "__main__":
    unittest.main()
