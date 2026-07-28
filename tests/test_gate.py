from astor.eval import gate


def test_gate_passes_when_all_bars_met():
    r = gate.gate_decision(precision=0.95, kind_accuracy=0.80, exact_rate=0.10)
    assert r.passed is True
    assert r.reasons == []


def test_gate_fails_on_low_precision():
    r = gate.gate_decision(precision=0.80, kind_accuracy=0.90, exact_rate=0.10)
    assert r.passed is False
    assert any("precision" in reason for reason in r.reasons)


def test_gate_fails_on_high_exact_rate():
    r = gate.gate_decision(precision=0.95, kind_accuracy=0.90, exact_rate=0.55)
    assert r.passed is False
    assert any("exact_rate" in reason for reason in r.reasons)


def test_gate_fails_when_kind_accuracy_is_none():
    r = gate.gate_decision(precision=0.95, kind_accuracy=None, exact_rate=0.10)
    assert r.passed is False
    assert any("kind_accuracy" in reason for reason in r.reasons)
