import uuid

from astor.api.skus import astor_sku


def test_astor_sku_from_uuid_object():
    pid = uuid.UUID("7f3a21b4-0000-0000-0000-000000000000")
    assert astor_sku(pid) == "ASR-7F3A21"


def test_astor_sku_from_string_is_stable():
    s = "7f3a21b4-0000-0000-0000-000000000000"
    assert astor_sku(s) == "ASR-7F3A21"
    assert astor_sku(s) == astor_sku(s)


def test_astor_sku_uppercases_hex():
    pid = uuid.UUID("abcdef12-0000-0000-0000-000000000000")
    assert astor_sku(pid) == "ASR-ABCDEF"
