"""Structural design engine for bonded anchors and post-installed reinforcement."""
from __future__ import annotations

from typing import Optional

from ..models import AdhesiveSpec, CaseResult, DesignCase
from .anchors import AnchorEngine
from .basis import DesignBasis
from .rebar import check_rebar_case


def check_case(case: DesignCase, spec: Optional[AdhesiveSpec] = None,
               basis: Optional[DesignBasis] = None) -> CaseResult:
    """Dispatch to the anchor (AS 5216) or post-installed rebar (App. D) engine."""
    basis = basis or DesignBasis()
    if case.kind == "rebar":
        return check_rebar_case(case, spec, basis)
    return AnchorEngine(case, spec, basis).run()


__all__ = ["check_case", "DesignBasis", "AnchorEngine", "check_rebar_case"]
