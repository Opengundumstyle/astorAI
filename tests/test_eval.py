from astor.eval.accuracy import match_metrics, prf, cosine
import numpy as np


def test_prf_basic():
    p, r, f = prf(tp=8, fp=2, fn=2)
    assert p == 0.8 and r == 0.8 and round(f, 4) == 0.8


def test_match_metrics_counts_and_kind_accuracy():
    pred = ["exact", "substitute", "none", "substitute"]
    gold = ["exact", "substitute", "none", "none"]
    m = match_metrics(pred, gold)
    # positives predicted: exact, substitute, substitute -> tp for first two, fp for last
    assert m["tp"] == 2 and m["fp"] == 1 and m["fn"] == 0 and m["tn"] == 1
    # kind accuracy over the 2 gold positives: both predicted correctly
    assert m["kind_accuracy"] == 1.0


def test_cosine_orthogonal_and_parallel():
    assert cosine(np.array([1.0, 0]), np.array([0, 1.0])) == 0.0
    assert round(cosine(np.array([1.0, 2]), np.array([2.0, 4])), 4) == 1.0
