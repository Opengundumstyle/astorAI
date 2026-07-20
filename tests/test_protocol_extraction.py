"""Materials extraction — offline tests (no network, no API key needed).

The behaviour that matters here is the procurement filter: a protocol's free-text
materials list contains section headers and buffer components as well as products,
and only the products may reach a quote.
"""
from __future__ import annotations

import pytest

from astor.protocols import extraction
from astor.protocols.extraction import (
    ExtractedMaterial,
    ExtractedMaterials,
    LLMMaterialExtractor,
    MaterialRole,
    StructuredMaterialExtractor,
    for_protocol,
    procurement_filter,
)
from astor.protocols.schemas import RawMaterial, RawProtocol, RawStep


def _raw(materials: list[RawMaterial], **over) -> RawProtocol:
    base = dict(
        source="protocols.io", source_id="1", source_uri="u",
        title="Total RNA extraction",
        steps=[RawStep(number=1, text="Lyse the cells in TRIzol.")],
        materials=materials,
    )
    base.update(over)
    return RawProtocol(**base)


def _mat(name, role, confidence=1.0, **over) -> ExtractedMaterial:
    return ExtractedMaterial(name=name, role=role, confidence=confidence, **over)


# --------------------------------------------------------------------------- #
# Role semantics
# --------------------------------------------------------------------------- #
def test_only_orderable_roles_are_purchasable():
    assert MaterialRole.KIT.purchasable
    assert MaterialRole.REAGENT.purchasable
    assert MaterialRole.CONSUMABLE.purchasable
    # Real but not per-run procurement demand:
    assert not MaterialRole.EQUIPMENT.purchasable
    # Made in-house, not ordered as written:
    assert not MaterialRole.BUFFER_COMPONENT.purchasable
    assert not MaterialRole.NOT_A_MATERIAL.purchasable


def test_procurement_filter_drops_the_real_world_noise():
    """These four lines are verbatim from a live protocols.io materials_text block —
    only one of them is something a lab can order."""
    got = procurement_filter([
        _mat("Reagents", MaterialRole.NOT_A_MATERIAL),
        _mat("Extraction Buffer:", MaterialRole.NOT_A_MATERIAL),
        _mat("100 mM Tris-HCl pH 8.2", MaterialRole.BUFFER_COMPONENT),
        _mat("TRIzol Reagent", MaterialRole.REAGENT),
    ])
    assert [m.name for m in got] == ["TRIzol Reagent"]


def test_procurement_filter_drops_low_confidence_lines():
    """A wrong line item costs more than a missing one — someone has to catch it."""
    got = procurement_filter([
        _mat("Probably a reagent?", MaterialRole.REAGENT, confidence=0.2),
        _mat("TRIzol Reagent", MaterialRole.REAGENT, confidence=0.9),
    ])
    assert [m.name for m in got] == ["TRIzol Reagent"]


# --------------------------------------------------------------------------- #
# Extractor selection
# --------------------------------------------------------------------------- #
def test_structured_source_skips_the_llm_entirely():
    raw = _raw([RawMaterial(name="RNeasy® Mini Kit", vendor="Qiagen", catalog_no="74104")])
    assert isinstance(for_protocol(raw), StructuredMaterialExtractor)


def test_free_text_source_uses_the_llm():
    raw = _raw([RawMaterial(name="1.5 mL Eppendorf tubes")])
    assert isinstance(for_protocol(raw), LLMMaterialExtractor)


def test_structured_extractor_preserves_vendor_and_catalog():
    raw = _raw([
        RawMaterial(name="RNeasy® Mini Kit", vendor="Qiagen", catalog_no="74104"),
        RawMaterial(name="TRIzol Reagent", vendor="Invitrogen", catalog_no="15596026"),
        RawMaterial(name="a bare line with no vendor"),   # dropped: nothing to match on
    ])
    got = StructuredMaterialExtractor().extract(raw)
    assert [(m.name, m.vendor, m.catalog_no) for m in got] == [
        ("RNeasy® Mini Kit", "Qiagen", "74104"),
        ("TRIzol Reagent", "Invitrogen", "15596026"),
    ]
    assert got[0].role is MaterialRole.KIT       # name contains "kit"
    assert got[1].role is MaterialRole.REAGENT
    assert all(m.confidence == 1.0 for m in got)


# --------------------------------------------------------------------------- #
# LLM path — stubbed, never hits the network
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


@pytest.fixture
def stub_anthropic(monkeypatch):
    """Patch the SDK constructor and hand back the captured client."""
    holder = {}

    def install(response):
        client = _FakeClient(response)
        holder["client"] = client
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: client)
        monkeypatch.setattr(extraction.settings, "anthropic_api_key", "sk-test")
        return client

    return install


def test_llm_extractor_returns_parsed_materials(stub_anthropic):
    client = stub_anthropic(_FakeResponse(ExtractedMaterials(materials=[
        _mat("Reagents", MaterialRole.NOT_A_MATERIAL),
        _mat("TRIzol Reagent", MaterialRole.REAGENT, vendor="Invitrogen"),
    ])))
    raw = _raw([RawMaterial(name="Reagents"), RawMaterial(name="TRIzol Reagent")])

    got = LLMMaterialExtractor().extract(raw)
    assert [m.name for m in got] == ["Reagents", "TRIzol Reagent"]
    assert [m.name for m in procurement_filter(got)] == ["TRIzol Reagent"]

    sent = client.messages.kwargs
    assert sent["model"] == "claude-opus-4-8"
    assert sent["output_format"] is ExtractedMaterials
    assert sent["thinking"] == {"type": "adaptive"}
    # Both materials lines must reach the model — dropping one silently loses a SKU.
    assert "Reagents" in sent["messages"][0]["content"]
    assert "TRIzol Reagent" in sent["messages"][0]["content"]


def test_llm_extractor_passes_steps_as_context_only(stub_anthropic):
    client = stub_anthropic(_FakeResponse(ExtractedMaterials()))
    LLMMaterialExtractor().extract(_raw([RawMaterial(name="TRIzol")]))
    prompt = client.messages.kwargs["messages"][0]["content"]
    assert "Lyse the cells in TRIzol." in prompt
    assert "do not extract materials from here" in prompt


def test_llm_extractor_returns_nothing_on_refusal(stub_anthropic):
    stub_anthropic(_FakeResponse(ExtractedMaterials(), stop_reason="refusal"))
    assert LLMMaterialExtractor().extract(_raw([RawMaterial(name="x")])) == []


def test_llm_extractor_skips_the_call_when_there_are_no_materials(monkeypatch):
    """No API key set and none needed — the empty case must not reach the client."""
    monkeypatch.setattr(extraction.settings, "anthropic_api_key", None)
    assert LLMMaterialExtractor().extract(_raw([])) == []


def test_llm_extractor_requires_an_api_key(monkeypatch):
    monkeypatch.setattr(extraction.settings, "anthropic_api_key", None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        LLMMaterialExtractor().extract(_raw([RawMaterial(name="TRIzol")]))
