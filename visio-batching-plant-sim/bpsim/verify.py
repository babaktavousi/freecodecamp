"""Verification module: rules + AI, step-by-step corrections, final re-verification."""
from __future__ import annotations

from dataclasses import dataclass, field

from .ai import AIClient, AIResult
from .fixes import apply_action
from .model import PlantModel
from .rules import Fix, Issue, is_complete, verify_rules


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    ai: AIResult | None = None
    ai_overridden: bool = False

    @property
    def rule_errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ai_blocking(self) -> bool:
        return bool(self.ai and self.ai.available and self.ai.complete is False and not self.ai_overridden)

    @property
    def complete(self) -> bool:
        return not self.rule_errors and not self.ai_blocking

    @property
    def ai_checked(self) -> bool:
        return bool(self.ai and self.ai.available)

    def status_text(self) -> str:
        if self.complete:
            return ("MODEL VERIFIED - complete (rule check + AI check passed)." if self.ai_checked else
                    "MODEL VERIFIED by rule check. AI check was not available" +
                    (f" ({self.ai.error})." if self.ai and self.ai.error else "."))
        n = len(self.rule_errors)
        return f"MODEL INCOMPLETE / DEFECTIVE - {n} rule error(s)" + \
               (", AI reports the model is not complete." if self.ai_blocking else ".")


def run_verification(m: PlantModel, ai: AIClient | None, use_ai: bool = True) -> Report:
    issues = verify_rules(m)
    rep = Report(issues=issues)
    if use_ai and ai is not None:
        rep.ai = ai.analyze_model(m, issues)
    return rep


def plan_rule_fixes(m: PlantModel, skipped: set[str] | None = None, limit: int = 80) -> list[Fix]:
    """Simulate the corrections on a copy of the model to obtain the full ordered plan."""
    skipped = skipped or set()
    work, plan = m.copy(), []
    for _ in range(limit):
        step = None
        for iss in verify_rules(work):
            if iss.severity == "info":
                continue
            step = next((f for f in iss.fixes if not f.manual and f.key not in skipped), None)
            if step:
                break
        if step is None:
            break
        before = work.signature()
        if not all(apply_action(work, a)[0] for a in step.actions) or work.signature() == before:
            skipped = skipped | {step.key}
            continue
        plan.append(step)
    return plan


def manual_issues(m: PlantModel) -> list[Issue]:
    return [i for i in verify_rules(m) if i.severity != "info" and i.fixes and all(f.manual for f in i.fixes)]


class CorrectionSession:
    """Walks the user through corrections one step at a time and re-plans after every decision."""

    def __init__(self, model: PlantModel, ai_steps: list[Fix] | None = None) -> None:
        self.model = model
        self.skipped: set[str] = set()
        self.ai_steps = list(ai_steps or [])
        self.log: list[str] = []

    def plan(self) -> list[Fix]:
        p = plan_rule_fixes(self.model, self.skipped)
        if not p:     # rules are satisfied -> offer remaining AI-only suggestions
            work = self.model.copy()
            for st in self.ai_steps:
                if st.key in self.skipped:
                    continue
                t = work.copy()
                if all(apply_action(t, a)[0] for a in st.actions) and t.signature() != work.signature():
                    work = t
                    p.append(st)
        return p

    def apply(self, fix: Fix) -> tuple[bool, str]:
        trial = self.model.copy()
        msgs = []
        for a in fix.actions:
            ok, msg = apply_action(trial, a)
            if not ok:
                self.skipped.add(fix.key)
                return False, msg
            msgs.append(msg)
        self.model.__dict__.update(trial.__dict__)
        self.ai_steps = [s for s in self.ai_steps if s.key != fix.key]
        self.log.append("; ".join(msgs))
        return True, "; ".join(msgs)

    def skip(self, fix: Fix) -> None:
        self.skipped.add(fix.key)
        self.log.append(f"skipped: {fix.title}")
