from astor.db.models import ProtocolMaterialLink

def test_link_table_shape():
    t = ProtocolMaterialLink.__table__
    assert t.name == "protocol_material_links"
    cols = set(t.columns.keys())
    assert {"protocol_id", "product_id", "material_name",
            "confidence", "kind", "method", "reviewed"} <= cols
    constraints = {c.name for c in t.constraints}
    assert "uq_protocol_material_link" in constraints
