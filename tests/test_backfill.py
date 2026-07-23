from astor.catalog import backfill


def test_text_hash_is_stable_and_sensitive():
    h1 = backfill.text_hash("Vazyme | 2x Taq Master Mix | molecular_biology")
    h2 = backfill.text_hash("Vazyme | 2x Taq Master Mix | molecular_biology")
    h3 = backfill.text_hash("NEB | 2X Taq PCR Master Mix | molecular_biology")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # sha256 hex


def test_is_stale_true_when_no_provenance():
    assert backfill.is_stale(None, None, "voyage-3", "any text") is True


def test_is_stale_true_when_model_differs():
    txt = "Vazyme | 2x Taq | molecular_biology"
    assert backfill.is_stale("dev", backfill.text_hash(txt), "voyage-3", txt) is True


def test_is_stale_true_when_text_changed():
    old = backfill.text_hash("old text")
    assert backfill.is_stale("voyage-3", old, "voyage-3", "new text") is True


def test_is_stale_false_when_model_and_text_match():
    txt = "Vazyme | 2x Taq | molecular_biology"
    assert backfill.is_stale("voyage-3", backfill.text_hash(txt), "voyage-3", txt) is False
