"""Tkinter front end: one window with the six modules (model, verification, equipment data, simulation, results, AI settings)."""
from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import catalog as C
from . import rates
from .ai import PRESETS, AIClient, AIConfig
from .analysis import Analysis, analyse, apply_mod, report_dict, report_text
from .fixes import describe_action
from .model import PlantModel
from .simulate import SimSettings
from .verify import CorrectionSession, Report, manual_issues, run_verification
from .visio_reader import load_any, read_visio_live, tag_shapes_live

OK, BAD, WARN = "#1b7f3b", "#b3261e", "#9a6700"


class App(tk.Tk):
    def __init__(self, argv: list[str] | None = None) -> None:
        super().__init__()
        self.title("Batching Plant Simulator for Visio")
        self.geometry("1100x760")
        self.minsize(900, 600)
        self.model: PlantModel | None = None
        self.live = False
        self.report: Report | None = None
        self.session: CorrectionSession | None = None
        self.verified = False
        self.analysis: Analysis | None = None
        self._prog_msg = ""
        self.ai_cfg = AIConfig.load()
        self._build()
        argv = argv or []
        if argv and not argv[0].startswith("-"):
            self.after(200, lambda: self.open_file(argv[0]))
        elif "--live" in argv:
            self.after(200, self.read_live)
        self._refresh_gates()

    # ================================================================ layout
    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("Banner.TLabel", font=("Segoe UI", 11, "bold"), padding=8)
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Button(top, text="Read open Visio page", command=self.read_live).pack(side="left")
        ttk.Button(top, text="Open .vsdx / .json…", command=self.open_file).pack(side="left", padx=4)
        ttk.Button(top, text="AI settings…", command=self.ai_settings).pack(side="right")
        self.src_lbl = ttk.Label(top, text="No drawing loaded")
        self.src_lbl.pack(side="left", padx=12)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tabs = {}
        for key, title in (("model", "1 · Model"), ("verify", "2 · Verification"), ("data", "3 · Equipment data"),
                           ("sim", "4 · Simulation"), ("result", "5 · Bottlenecks & plan")):
            f = ttk.Frame(self.nb, padding=8)
            self.nb.add(f, text=title)
            self.tabs[key] = f
        self._tab_model(self.tabs["model"])
        self._tab_verify(self.tabs["verify"])
        self._tab_data(self.tabs["data"])
        self._tab_sim(self.tabs["sim"])
        self._tab_result(self.tabs["result"])
        self.status = ttk.Label(self, anchor="w", relief="sunken", padding=3)
        self.status.pack(fill="x", side="bottom")

    @staticmethod
    def _tree(parent, cols, widths, height=10):
        fr = ttk.Frame(parent)
        t = ttk.Treeview(fr, columns=cols, show="headings", height=height)
        for c, w in zip(cols, widths):
            t.heading(c, text=c)
            t.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(fr, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return fr, t

    # ---- tab 1 ---------------------------------------------------------
    def _tab_model(self, p) -> None:
        ttk.Label(p, text="Equipment and connections read from the drawing. Check that every shape was recognised; "
                          "change a wrong type below.", wraplength=1000).pack(anchor="w")
        fr, self.eq_tree = self._tree(p, ("ID", "Type", "Label", "Material", "Feeds from", "Feeds to"),
                                      (80, 190, 230, 90, 190, 190), 12)
        fr.pack(fill="both", expand=True, pady=6)
        row = ttk.Frame(p)
        row.pack(fill="x")
        ttk.Label(row, text="Set type of selected:").pack(side="left")
        self.type_var = tk.StringVar()
        ttk.Combobox(row, textvariable=self.type_var, state="readonly", width=34,
                     values=[s.label for s in C.TYPES.values()]).pack(side="left", padx=4)
        ttk.Button(row, text="Apply", command=self.set_type).pack(side="left")
        ttk.Button(row, text="Write IDs/types to Visio shapes", command=self.tag_shapes).pack(side="right")
        ttk.Button(row, text="Save model (JSON)…", command=self.save_json).pack(side="right", padx=4)
        self.read_notes = tk.Text(p, height=5, wrap="word", state="disabled")
        self.read_notes.pack(fill="x", pady=(6, 0))

    # ---- tab 2 ---------------------------------------------------------
    def _tab_verify(self, p) -> None:
        bar = ttk.Frame(p)
        bar.pack(fill="x")
        ttk.Button(bar, text="Run verification", command=self.verify).pack(side="left")
        self.use_ai = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Use free AI check", variable=self.use_ai).pack(side="left", padx=10)
        self.draw_visio = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Also draw corrections in Visio", variable=self.draw_visio).pack(side="left")
        self.banner = ttk.Label(p, text="Not verified yet", style="Banner.TLabel", anchor="w")
        self.banner.pack(fill="x", pady=6)
        fr, self.issue_tree = self._tree(p, ("Severity", "Source", "Finding"), (80, 70, 800), 7)
        fr.pack(fill="both", expand=True)
        box = ttk.LabelFrame(p, text="Step-by-step corrections", padding=8)
        box.pack(fill="x", pady=8)
        self.step_head = ttk.Label(box, text="Run verification to get correction steps.", font=("Segoe UI", 10, "bold"))
        self.step_head.pack(anchor="w")
        self.step_text = tk.Text(box, height=5, wrap="word", state="disabled")
        self.step_text.pack(fill="x", pady=4)
        b = ttk.Frame(box)
        b.pack(fill="x")
        self.btn_apply = ttk.Button(b, text="Apply this step", command=self.apply_step, state="disabled")
        self.btn_skip = ttk.Button(b, text="Skip", command=self.skip_step, state="disabled")
        self.btn_all = ttk.Button(b, text="Apply all remaining", command=self.apply_all, state="disabled")
        self.btn_final = ttk.Button(b, text="Final verification", command=self.verify, state="disabled")
        self.btn_override = ttk.Button(b, text="Accept despite AI remarks", command=self.override_ai, state="disabled")
        for w in (self.btn_apply, self.btn_skip, self.btn_all, self.btn_final, self.btn_override):
            w.pack(side="left", padx=(0, 6))
        self.current_fix = None

    # ---- tab 3 ---------------------------------------------------------
    def _tab_data(self, p) -> None:
        ttk.Label(p, text="Enter the performance capacity and rates of every equipment item. "
                          "The model must be verified first.", wraplength=1000).pack(anchor="w")
        bar = ttk.Frame(p)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Enter equipment data… (dialog)", command=self.open_data_dialog).pack(side="left")
        ttk.Button(bar, text="Mix recipe…", command=self.open_recipe).pack(side="left", padx=6)
        self.data_lbl = ttk.Label(bar, text="")
        self.data_lbl.pack(side="left", padx=10)
        fr, self.data_tree = self._tree(p, ("ID", "Type", "Data", "Status"), (80, 200, 640, 100), 16)
        fr.pack(fill="both", expand=True)

    # ---- tab 4 ---------------------------------------------------------
    def _tab_sim(self, p) -> None:
        g = ttk.LabelFrame(p, text="Discrete-event simulation settings", padding=8)
        g.pack(fill="x")
        self.sv = {}
        for i, (k, lbl, v) in enumerate((("duration_h", "Simulated time (h)", 8), ("warmup_h", "Warm-up excluded (h)", 0.5),
                                         ("wip", "Max batches in plant (WIP)", 4), ("replications", "Replications", 5),
                                         ("variability", "Duration variability ±%", 5), ("target", "Target output (m³/h, optional)", ""))):
            ttk.Label(g, text=lbl).grid(row=i // 3, column=(i % 3) * 2, sticky="e", padx=4, pady=3)
            self.sv[k] = tk.StringVar(value=str(v))
            ttk.Entry(g, textvariable=self.sv[k], width=8).grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w")
        bar = ttk.Frame(p)
        bar.pack(fill="x", pady=8)
        self.btn_run = ttk.Button(bar, text="Run simulation + bottleneck analysis", command=self.run_sim)
        self.btn_run.pack(side="left")
        ttk.Button(bar, text="Colour Visio shapes by load", command=self.annotate).pack(side="left", padx=6)
        self.prog = ttk.Label(bar, text="")
        self.prog.pack(side="left", padx=10)
        self.sum_lbl = ttk.Label(p, text="", style="Banner.TLabel")
        self.sum_lbl.pack(fill="x")
        fr, self.res_tree = self._tree(p, ("ID", "Type", "Units", "Busy %", "Blocked %", "Plant limit (m³/h)"),
                                       (80, 240, 60, 90, 90, 150), 16)
        fr.pack(fill="both", expand=True)

    # ---- tab 5 ---------------------------------------------------------
    def _tab_result(self, p) -> None:
        bar = ttk.Frame(p)
        bar.pack(fill="x")
        ttk.Button(bar, text="Apply recommended changes to model", command=self.apply_plan).pack(side="left")
        ttk.Button(bar, text="Ask AI to comment", command=self.ask_ai).pack(side="left", padx=6)
        ttk.Button(bar, text="Save report…", command=self.save_report).pack(side="right")
        self.rep_text = tk.Text(p, wrap="word", font=("Consolas", 10))
        self.rep_text.pack(fill="both", expand=True, pady=6)

    # ================================================================ helpers
    def say(self, msg: str) -> None:
        self.status.config(text=msg)
        self.update_idletasks()

    def bg(self, fn, done, busy: str = "Working…") -> None:
        q: queue.Queue = queue.Queue()
        self.say(busy)
        self.config(cursor="watch")

        def work():
            try:
                q.put((True, fn()))
            except Exception as exc:                      # noqa: BLE001 - shown to the user
                import traceback
                traceback.print_exc()
                q.put((False, exc))

        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                ok, val = q.get_nowait()
            except queue.Empty:
                self.prog.config(text=self._prog_msg)
                self.after(150, poll)
                return
            self.config(cursor="")
            if ok:
                done(val)
            else:
                self.say("Error")
                messagebox.showerror("Error", str(val))
        poll()

    def _refresh_gates(self) -> None:
        has = self.model is not None
        rdy = has and self.verified
        for key, enable in (("verify", has), ("data", rdy), ("sim", rdy and rates.all_final(self.model)),
                            ("result", self.analysis is not None)):
            self.nb.tab(self.tabs[key], state="normal" if enable else "disabled")
        if has:
            self.data_lbl.config(text=("All equipment data is final ✔" if rates.all_final(self.model)
                                       else f"{len(rates.needs_data(self.model))} item(s) still need data"),
                                 foreground=OK if rates.all_final(self.model) else WARN)

    def model_changed(self) -> None:
        """Any edit invalidates verification, equipment data and results."""
        self.verified = False
        self.analysis = None
        if self.model:
            rates.invalidate(self.model)
        self.fill_model()
        self._refresh_gates()

    # ================================================================ module 1
    def read_live(self) -> None:
        def done(m):
            self.live = True
            self.load_model(m, "open Visio page")
        self.bg(read_visio_live, done, "Reading the page open in Visio…")

    def open_file(self, path: str | None = None) -> None:
        path = path or filedialog.askopenfilename(filetypes=[("Visio / model", "*.vsdx *.vsdm *.json"), ("All", "*.*")])
        if not path:
            return
        try:
            m = load_any(path)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("Cannot read file", str(exc))
            return
        self.live = False
        self.load_model(m, path)

    def load_model(self, m: PlantModel, src: str) -> None:
        self.model, self.report, self.session = m, None, None
        self.src_lbl.config(text=f"{m.name}  [{src}]")
        self.model_changed()
        self.nb.select(self.tabs["model"])
        self.banner.config(text="Not verified yet", foreground="")
        self.issue_tree.delete(*self.issue_tree.get_children())
        self._set_step(None)
        self.say(f"Read {len(m.equipment)} equipment items and {len(m.connections)} connections.")

    def fill_model(self) -> None:
        m = self.model
        self.eq_tree.delete(*self.eq_tree.get_children())
        if not m:
            return
        for e in m.equipment.values():
            self.eq_tree.insert("", "end", iid=e.id, values=(e.id, e.spec.label if e.spec else "⚠ unknown", e.label,
                                                          e.material or "", ", ".join(m.pred(e.id)) or "–",
                                                          ", ".join(m.succ(e.id)) or "–"))
        notes = [f"⚠ {r['message']}" for r in m.read_issues] + [f"• not equipment: {x}" for x in m.ignored[:8]]
        self.read_notes.config(state="normal")
        self.read_notes.delete("1.0", "end")
        self.read_notes.insert("end", "\n".join(notes) or "All shapes were recognised.")
        self.read_notes.config(state="disabled")
        self.fill_data()

    def set_type(self) -> None:
        sel = self.eq_tree.selection()
        if not sel or not self.type_var.get():
            return
        key = next(k for k, s in C.TYPES.items() if s.label == self.type_var.get())
        from .fixes import apply_action
        for i in sel:
            apply_action(self.model, {"op": "set_type", "id": i, "type": key})
        self.model_changed()

    def tag_shapes(self) -> None:
        try:
            n = tag_shapes_live(self.model)
            messagebox.showinfo("Visio", f"Stored EquipID / EquipType / Material on {n} shape(s).")
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("Visio", str(exc))

    def save_json(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path and self.model:
            self.model.save(path)

    # ================================================================ module 2
    def verify(self) -> None:
        if not self.model:
            return
        ai = AIClient(self.ai_cfg) if self.use_ai.get() else None
        m = self.model
        prev = self.report.ai_overridden if self.report else False
        self.bg(lambda: run_verification(m, ai), lambda r: self.verified_done(r, prev),
                "Verifying model (rules" + (" + AI)…" if ai else ")…"))

    def verified_done(self, rep: Report, prev_override: bool) -> None:
        self.report = rep
        rep.ai_overridden = prev_override and rep.ai_blocking
        self.verified = rep.complete
        self.issue_tree.delete(*self.issue_tree.get_children())
        for i in rep.issues:
            self.issue_tree.insert("", "end", values=(i.severity, "rules", i.message))
        if rep.ai:
            if rep.ai.available:
                for i in rep.ai.issues:
                    self.issue_tree.insert("", "end", values=(i.severity, "AI", i.message))
                if rep.ai.summary:
                    self.issue_tree.insert("", "end", values=("info", "AI", rep.ai.summary))
            else:
                self.issue_tree.insert("", "end", values=("info", "AI", f"AI check unavailable: {rep.ai.error}"))
        self.banner.config(text=rep.status_text(), foreground=OK if rep.complete else BAD)
        if rep.complete:
            self._set_step(None)
            self.session = None
            self.btn_final.config(state="normal")
            self.say("Model verified. Continue with 3 · Equipment data.")
            self.fill_data()
            self._refresh_gates()
            self.nb.select(self.tabs["data"])
            return
        if self.session is None:
            self.session = CorrectionSession(self.model, rep.ai.steps if rep.ai else [])
        else:
            self.session.ai_steps = list(rep.ai.steps if rep.ai else [])
        self.btn_override.config(state="normal" if rep.ai_blocking and not rep.rule_errors else "disabled")
        self.next_step()
        self._refresh_gates()

    def next_step(self) -> None:
        plan = self.session.plan() if self.session else []
        if plan:
            self._set_step(plan[0], len(plan))
            return
        self._set_step(None)
        left = manual_issues(self.model)
        txt = "No automatic corrections left."
        if left:
            txt += " Fix these in Visio, then click 'Read open Visio page':\n" + "\n".join(
                f" • {i.message} - {i.fixes[0].actions[0].get('text', '')}" for i in left)
        self._write_step(txt)
        self.btn_final.config(state="normal")

    def _write_step(self, txt: str) -> None:
        self.step_text.config(state="normal")
        self.step_text.delete("1.0", "end")
        self.step_text.insert("end", txt)
        self.step_text.config(state="disabled")

    def _set_step(self, fix, remaining: int = 0) -> None:
        self.current_fix = fix
        st = "normal" if fix else "disabled"
        for b in (self.btn_apply, self.btn_skip, self.btn_all):
            b.config(state=st)
        if not fix:
            self.step_head.config(text="Corrections" if self.session else "Run verification to get correction steps.")
            if not self.session:
                self._write_step("")
            return
        done = len(self.session.log)
        self.step_head.config(text=f"Step {done + 1} of {done + remaining}: {fix.title}"
                                   + ("   [suggested by AI]" if fix.origin == "ai" else ""))
        self._write_step(f"Why: {fix.why}\n\nWhat will change:\n" + "\n".join(f"  • {describe_action(a)}" for a in fix.actions))
        self.btn_final.config(state="normal")

    def apply_step(self) -> bool:
        fix = self.current_fix
        if not fix:
            return False
        ok, msg = self.session.apply(fix)
        if not ok:
            messagebox.showwarning("Step not applied", msg)
        else:
            self.verified = False
            rates.invalidate(self.model)
            self.fill_model()
            self.say(msg)
        self.next_step()
        if not self.session.plan():
            self.verify()              # final verification after the last correction
        return ok

    def skip_step(self) -> None:
        if self.current_fix:
            self.session.skip(self.current_fix)
            self.next_step()

    def apply_all(self) -> None:
        guard = 0
        while self.current_fix and guard < 100:
            guard += 1
            fix = self.current_fix
            ok, _ = self.session.apply(fix)
            if not ok:
                break
            plan = self.session.plan()
            self.current_fix = plan[0] if plan else None
            self.step_head.config(text="Applying…")
        self.verified = False
        rates.invalidate(self.model)
        self.fill_model()
        if self.draw_visio.get() and self.live:
            self.sync_visio()
        self.verify()

    def sync_visio(self) -> None:
        from .visio_writer import sync_model_to_visio
        try:
            log = sync_model_to_visio(self.model)
            self.say(f"Visio updated ({len(log)} change(s)).")
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("Visio", str(exc))

    def override_ai(self) -> None:
        if self.report and messagebox.askyesno("Accept model", "The rule check passed but the AI still has remarks.\n"
                                               "Accept the model as complete anyway?"):
            self.report.ai_overridden = True
            self.verified_done(self.report, True)

    # ================================================================ module 3
    def fill_data(self) -> None:
        self.data_tree.delete(*self.data_tree.get_children())
        if not self.model:
            return
        for e in self.model.equipment.values():
            if not e.spec:
                continue
            info = ", ".join(f"{p.label.split('(')[0].strip()}: {e.params[p.key]:g} {p.unit}"
                             for p in e.spec.params if p.key in e.params) or ("no data needed" if not e.spec.params else "–")
            ok = e.finalized or not e.spec.params
            self.data_tree.insert("", "end", values=(e.id, e.spec.label, info, "✔ final" if ok else "needed"))

    def open_data_dialog(self) -> None:
        if self.model and self.verified:
            EquipmentDialog(self, self.model)
            self.fill_data()
            self._refresh_gates()

    def open_recipe(self) -> None:
        if self.model:
            RecipeDialog(self, self.model)

    # ================================================================ module 4
    def settings(self) -> SimSettings:
        g = lambda k: self.sv[k].get().strip()
        return SimSettings(duration_h=float(g("duration_h")), warmup_h=float(g("warmup_h")), wip=int(float(g("wip"))),
                           replications=int(float(g("replications"))), variability=float(g("variability")) / 100.0)

    def run_sim(self) -> None:
        try:
            st = self.settings()
            target = float(self.sv["target"].get()) if self.sv["target"].get().strip() else None
        except ValueError:
            messagebox.showerror("Settings", "Please enter numbers in the simulation settings.")
            return
        m = self.model.copy()
        self.btn_run.config(state="disabled")

        def work():
            return analyse(m, st, target, progress=lambda s: setattr(self, "_prog_msg", s[:90]))

        def done(a: Analysis):
            self.btn_run.config(state="normal")
            self.prog.config(text="")
            self.analysis = a
            self.show_results()
        self.bg(work, done, "Running discrete-event simulation and testing modifications…")

    def show_results(self) -> None:
        a = self.analysis
        self.res_tree.delete(*self.res_tree.get_children())
        r = a.result
        if r.error:
            self.sum_lbl.config(text=f"Simulation error: {r.error}", foreground=BAD)
            return
        self.sum_lbl.config(text=f"Overall production capacity {r.throughput_m3h:.1f} m³/h · bottleneck {r.bottleneck} · "
                                 f"{r.batches_per_h:.1f} batches/h of {r.batch_m3:.2f} m³", foreground=OK)
        for s in a.ranking:
            lim = "–" if s.limit_m3h == float("inf") else f"{s.limit_m3h:.1f}"
            self.res_tree.insert("", "end", values=(s.id, C.TYPES[s.type].label, s.units, f"{100 * s.util:.0f}",
                                                    f"{100 * s.blocked:.0f}", lim),
                                 tags=("hot",) if s.id == r.bottleneck else ())
        self.res_tree.tag_configure("hot", background="#fde2e0")
        self.rep_text.delete("1.0", "end")
        self.rep_text.insert("end", report_text(self.model, a))
        self._refresh_gates()
        self.nb.select(self.tabs["result"])
        self.say("Simulation finished.")

    def annotate(self) -> None:
        if not self.analysis:
            messagebox.showinfo("Visio", "Run the simulation first.")
            return
        from .visio_writer import annotate_results
        try:
            n = annotate_results(self.model, self.analysis.result)
            messagebox.showinfo("Visio", f"Coloured {n} shape(s): green < 60 %, amber < 85 %, red ≥ 85 % busy.")
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("Visio", str(exc))

    # ================================================================ module 5
    def apply_plan(self) -> None:
        a = self.analysis
        if not a or not a.plan:
            messagebox.showinfo("Plan", "There are no recommended changes.")
            return
        if messagebox.askyesno("Apply", "Write the recommended equipment changes into the model and re-run?\n"
                                        "(Your Visio drawing is not changed.)"):
            for s in a.plan:
                apply_mod(self.model, s.mod)
            self.fill_data()
            self.run_sim()

    def ask_ai(self) -> None:
        if not self.analysis:
            return
        ai = AIClient(self.ai_cfg)
        summary = json.dumps(report_dict(self.model, self.analysis))[:6000]

        def work():
            return ai.chat("You are a concrete batching plant process engineer. In under 200 words, explain the "
                           "simulation result and the order in which to implement the changes, with practical caveats.",
                           summary)
        self.bg(work, lambda t: (self.rep_text.insert("end", "\n\nAI COMMENT\n" + "-" * 11 + "\n" + t), self.rep_text.see("end")),
                "Asking the AI…")

    def save_report(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt"), ("JSON", "*.json")])
        if not path or not self.analysis:
            return
        with open(path, "w", encoding="utf-8") as f:
            if path.endswith(".json"):
                json.dump(report_dict(self.model, self.analysis), f, indent=2)
            else:
                f.write(self.rep_text.get("1.0", "end"))

    # ================================================================ AI settings
    def ai_settings(self) -> None:
        AISettingsDialog(self)


# ===================================================================== dialogs
class EquipmentDialog(tk.Toplevel):
    """Asks for the capacity / rates of every equipment, one item at a time."""

    def __init__(self, app: App, model: PlantModel) -> None:
        super().__init__(app)
        self.title("Equipment performance data")
        self.geometry("780x520")
        self.transient(app)
        self.grab_set()
        self.m = model
        self.items = [e for e in model.equipment.values() if e.spec and e.spec.params]
        self.idx = 0
        self.vars: dict[str, tk.StringVar] = {}
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        self.lb = tk.Listbox(left, width=34, activestyle="none", exportselection=False)
        self.lb.pack(fill="y", expand=True)
        self.lb.bind("<<ListboxSelect>>", lambda _e: self.goto(self.lb.curselection()[0]) if self.lb.curselection() else None)
        self.form = ttk.Frame(self, padding=14)
        self.form.pack(side="left", fill="both", expand=True)
        self.head = ttk.Label(self.form, font=("Segoe UI", 12, "bold"))
        self.head.pack(anchor="w")
        self.sub = ttk.Label(self.form, foreground="#555", wraplength=460)
        self.sub.pack(anchor="w", pady=(0, 8))
        self.grid_fr = ttk.Frame(self.form)
        self.grid_fr.pack(fill="x")
        self.err = ttk.Label(self.form, foreground=BAD, wraplength=460)
        self.err.pack(anchor="w", pady=6)
        bar = ttk.Frame(self.form)
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="◀ Back", command=lambda: self.goto(self.idx - 1)).pack(side="left")
        ttk.Button(bar, text="Use suggested values", command=self.defaults).pack(side="left", padx=6)
        self.next_btn = ttk.Button(bar, text="Confirm & next ▶", command=self.confirm)
        self.next_btn.pack(side="right")
        self.done_lbl = ttk.Label(self.form)
        self.done_lbl.pack(side="bottom", anchor="w", pady=6)
        self.refresh_list()
        self.goto(next((i for i, e in enumerate(self.items) if not e.finalized), 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.wait_window(self)

    def refresh_list(self) -> None:
        self.lb.delete(0, "end")
        for e in self.items:
            self.lb.insert("end", f"{'✔' if e.finalized else '○'}  {e.id} · {e.spec.label}")

    def goto(self, i: int) -> None:
        if not self.items:
            return
        self.idx = max(0, min(i, len(self.items) - 1))
        e = self.items[self.idx]
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(self.idx)
        self.head.config(text=f"{e.id} - {e.spec.label}")
        fams = ", ".join(sorted(self.m.families_upstream(e.id))) or "-"
        self.sub.config(text=f"{e.label}   |   material: {e.material or fams}   |   item {self.idx + 1} of {len(self.items)}")
        for w in self.grid_fr.winfo_children():
            w.destroy()
        sug = rates.suggest_defaults(self.m, e)
        self.vars = {}
        for r, p in enumerate(e.spec.params):
            ttk.Label(self.grid_fr, text=p.label).grid(row=r, column=0, sticky="w", pady=4)
            v = tk.StringVar(value=f"{e.params.get(p.key, sug[p.key]):g}")
            self.vars[p.key] = v
            ttk.Entry(self.grid_fr, textvariable=v, width=12).grid(row=r, column=1, padx=8)
            ttk.Label(self.grid_fr, text=p.unit).grid(row=r, column=2, sticky="w")
            if p.help:
                ttk.Label(self.grid_fr, text=p.help, foreground="#666").grid(row=r, column=3, sticky="w", padx=8)
        self.err.config(text="")
        last = self.idx == len(self.items) - 1
        self.next_btn.config(text="Confirm & finish ✔" if last else "Confirm & next ▶")
        n = sum(1 for x in self.items if x.finalized)
        self.done_lbl.config(text=f"{n} of {len(self.items)} items confirmed")

    def defaults(self) -> None:
        e = self.items[self.idx]
        for k, v in rates.suggest_defaults(self.m, e).items():
            self.vars[k].set(f"{v:g}")

    def confirm(self) -> None:
        e = self.items[self.idx]
        vals = {k: v.get().strip() for k, v in self.vars.items()}
        errs = rates.validate(e, vals)
        if errs:
            self.err.config(text="\n".join(errs))
            return
        e.params.update({k: float(v) for k, v in vals.items()})
        e.finalized = True
        self.refresh_list()
        left = [i for i, x in enumerate(self.items) if not x.finalized]
        if not left:
            for x in self.m.equipment.values():
                if x.spec and not x.spec.params:
                    x.finalized = True
            messagebox.showinfo("Equipment data", "All equipment data is final. You can run the simulation now.", parent=self)
            self.destroy()
            return
        self.goto(next((i for i in left if i > self.idx), left[0]))

    def close(self) -> None:
        self.destroy()


class RecipeDialog(tk.Toplevel):
    def __init__(self, app: App, model: PlantModel) -> None:
        super().__init__(app)
        self.title("Concrete mix recipe (kg per m³)")
        self.transient(app)
        self.grab_set()
        self.m = model
        eff = model.effective_recipe()
        self.vars = {}
        for r, (k, v) in enumerate(eff.items()):
            ttk.Label(self, text=k.capitalize()).grid(row=r, column=0, padx=10, pady=4, sticky="w")
            self.vars[k] = tk.StringVar(value=f"{model.recipe.get(k, v):g}")
            ttk.Entry(self, textvariable=self.vars[k], width=10).grid(row=r, column=1)
            ttk.Label(self, text="kg/m³").grid(row=r, column=2, padx=6)
        ttk.Button(self, text="OK", command=self.ok).grid(row=len(eff), column=0, columnspan=3, pady=10)
        self.wait_window(self)

    def ok(self) -> None:
        try:
            self.m.recipe = {k: float(v.get()) for k, v in self.vars.items()}
        except ValueError:
            messagebox.showerror("Recipe", "Enter numbers only.", parent=self)
            return
        self.destroy()


class AISettingsDialog(tk.Toplevel):
    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.title("Free AI provider")
        self.transient(app)
        self.grab_set()
        self.app, self.cfg = app, app.ai_cfg
        self.vars = {k: tk.StringVar(value=str(getattr(self.cfg, k))) for k in ("provider", "base_url", "model", "api_key")}
        ttk.Label(self, text="Any OpenAI-compatible endpoint works. Pollinations needs no key (shared free quota); "
                             "Groq / Gemini / OpenRouter need a free key; Ollama runs locally.",
                  wraplength=460).grid(row=0, column=0, columnspan=2, padx=10, pady=8)
        cb = ttk.Combobox(self, textvariable=self.vars["provider"], values=list(PRESETS) + ["custom"], state="readonly")
        rows = (("Provider", cb), ("Base URL", ttk.Entry(self, textvariable=self.vars["base_url"], width=52)),
                ("Model", ttk.Entry(self, textvariable=self.vars["model"], width=52)),
                ("API key", ttk.Entry(self, textvariable=self.vars["api_key"], width=52, show="•")))
        for r, (lbl, w) in enumerate(rows, 1):
            ttk.Label(self, text=lbl).grid(row=r, column=0, sticky="e", padx=8, pady=3)
            w.grid(row=r, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", self.preset)
        self.msg = ttk.Label(self, wraplength=460)
        self.msg.grid(row=5, column=0, columnspan=2, pady=4)
        bar = ttk.Frame(self)
        bar.grid(row=6, column=0, columnspan=2, pady=8)
        ttk.Button(bar, text="Test connection", command=self.test).pack(side="left", padx=4)
        ttk.Button(bar, text="Save", command=self.save).pack(side="left", padx=4)

    def preset(self, _e=None) -> None:
        p = PRESETS.get(self.vars["provider"].get())
        if p:
            self.vars["base_url"].set(p["base_url"])
            self.vars["model"].set(p["model"])

    def _cfg(self) -> AIConfig:
        return AIConfig(self.vars["provider"].get(), self.vars["base_url"].get(), self.vars["api_key"].get(),
                        self.vars["model"].get(), self.cfg.timeout)

    def test(self) -> None:
        cfg = self._cfg()

        def work():
            return AIClient(cfg).chat("Answer in one word.", "Say OK")
        self.app.bg(work, lambda t: self.msg.config(text=f"Connected. Reply: {t[:60]}", foreground=OK), "Testing AI…")

    def save(self) -> None:
        self.app.ai_cfg = self._cfg()
        try:
            self.app.ai_cfg.save()
        except OSError:
            pass
        self.destroy()


def run(argv: list[str] | None = None) -> None:
    App(argv or sys.argv[1:]).mainloop()
