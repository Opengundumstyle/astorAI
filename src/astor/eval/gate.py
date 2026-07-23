"""Calibration gate: decide whether the re-embedded map is safe to auto-rebuild.

Pure decision here; the metric inputs are produced by the harness (labeled) and a
corpus-sample sanity pass (unlabeled) in the orchestrator. The labeled gold set is
tiny (8 pairs) -- this catches grossly-wrong thresholds, it does NOT certify the
16k map. See the design spec's Limitations section.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateBars:
    min_precision: float = 0.90
    min_kind_accuracy: float = 0.75
    max_exact_rate: float = 0.40


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def gate_decision(
    precision: float, kind_accuracy: float | None, exact_rate: float,
    bars: GateBars = GateBars(),
) -> GateResult:
    reasons: list[str] = []
    if precision < bars.min_precision:
        reasons.append(f"precision {precision:.3f} < {bars.min_precision}")
    if kind_accuracy is None:
        reasons.append("kind_accuracy is None (no positive pairs scored)")
    elif kind_accuracy < bars.min_kind_accuracy:
        reasons.append(f"kind_accuracy {kind_accuracy:.3f} < {bars.min_kind_accuracy}")
    if exact_rate > bars.max_exact_rate:
        reasons.append(f"exact_rate {exact_rate:.3f} > {bars.max_exact_rate}")
    return GateResult(passed=not reasons, reasons=reasons)
