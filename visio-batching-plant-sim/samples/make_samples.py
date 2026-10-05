"""Generates the sample plant models (JSON). Run:  python samples/make_samples.py"""
import json, pathlib

here = pathlib.Path(__file__).parent

def E(i, t, label, material=None, **params):
    d = {"id": i, "type": t, "label": label, "params": params}
    if material:
        d["material"] = material
    return d

equipment = [
    E("AGB-01", "aggregate_bin", "Sand bin", "sand", rate=120, capacity=40, units=1),
    E("AGB-02", "aggregate_bin", "Gravel bin 20mm", "gravel", rate=150, capacity=60, units=1),
    E("CNV-01", "belt_conveyor", "Aggregate weigh belt", rate=170, units=1),
    E("WGH-01", "weigh_hopper", "Aggregate weigh hopper", capacity=4200, settle_time_s=5, dump_time_s=8, units=1),
    E("CSL-01", "cement_silo", "Cement silo", "cement", rate=60, capacity=100, units=1),
    E("SCW-01", "screw_conveyor", "Cement screw", rate=45, units=1),
    E("WGH-02", "weigh_hopper", "Cement weigh hopper", capacity=900, settle_time_s=4, dump_time_s=6, units=1),
    E("WTK-01", "water_tank", "Water tank", "water", rate=60, capacity=20, units=1),
    E("PMP-01", "pump", "Water pump", rate=40, units=1),
    E("WGH-03", "weigh_hopper", "Water weigh hopper", capacity=500, settle_time_s=3, dump_time_s=5, units=1),
    E("ADT-01", "admixture_tank", "Admixture tank", "admixture", rate=1.5, capacity=1, units=1),
    E("PMP-02", "pump", "Admixture dosing pump", rate=0.6, units=1),
    E("WGH-04", "weigh_hopper", "Admixture weigh hopper", capacity=20, settle_time_s=3, dump_time_s=4, units=1),
    E("MIX-01", "mixer", "Twin-shaft mixer 2 m3", capacity=2.0, mix_time_s=45, discharge_time_s=20, units=1),
    E("DCH-01", "discharge_hopper", "Discharge hopper", capacity=3, rate=120, units=1),
    E("TRK-01", "loadout", "Truck loadout bay", rate=80, units=1),
    E("CTL-01", "controller", "Batching PLC"),
    E("DCL-01", "dust_collector", "Silo dust filter"),
]
flow = [("AGB-01", "CNV-01"), ("AGB-02", "CNV-01"), ("CNV-01", "WGH-01"), ("WGH-01", "MIX-01"),
        ("CSL-01", "SCW-01"), ("SCW-01", "WGH-02"), ("WGH-02", "MIX-01"),
        ("WTK-01", "PMP-01"), ("PMP-01", "WGH-03"), ("WGH-03", "MIX-01"),
        ("ADT-01", "PMP-02"), ("PMP-02", "WGH-04"), ("WGH-04", "MIX-01"),
        ("MIX-01", "DCH-01"), ("DCH-01", "TRK-01")]
control = [("CTL-01", x) for x in ("MIX-01", "WGH-01", "WGH-02", "WGH-03", "WGH-04")] + [("CSL-01", "DCL-01")]
complete = {"name": "Complete 120 m3/h-class plant (sample)", "equipment": equipment,
            "connections": [{"from": a, "to": b, "kind": "flow"} for a, b in flow] +
                           [{"from": a, "to": b, "kind": "control"} for a, b in control],
            "recipe": {"cement": 350, "sand": 800, "gravel": 1000, "water": 175, "admixture": 3.5}}
json.dump(complete, open(here / "sample_plant_complete.json", "w"), indent=2)

# Defective variant: no controller, no water hopper/pump line, cement bypasses its hopper,
# gravel bin orphaned, reversed conveyor connection, mixer has no output, params removed.
bad_eq = [dict(e, params={}) for e in equipment if e["id"] not in ("CTL-01", "WGH-03", "PMP-01", "DCH-01", "TRK-01", "DCL-01")]
bad_flow = [("CNV-01", "AGB-01"), ("CNV-01", "WGH-01"), ("WGH-01", "MIX-01"),      # CNV-01 drawn backwards
            ("CSL-01", "SCW-01"), ("SCW-01", "MIX-01"),                                # no cement hopper
            ("ADT-01", "PMP-02"), ("PMP-02", "WGH-04"), ("WGH-04", "MIX-01")]
defective = {"name": "Defective plant (sample)", "equipment": bad_eq,
             "connections": [{"from": a, "to": b, "kind": "flow"} for a, b in bad_flow]}
json.dump(defective, open(here / "sample_plant_defective.json", "w"), indent=2)
print("samples written")
