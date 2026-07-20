"""Turn a protocol's materials list into procurement-ready facts.

WHY THIS EXISTS
    protocols.io returns materials two ways, and only one of them is usable:

      * STRUCTURED  `materials: [{name, sku, vendor: {name}, url, cas_number, rrid}]`
        -> already a (brand, mpn) pair. No LLM needed; pass it through.
      * FREE TEXT   `materials: []` with the real list in Draft.js `materials_text`
        -> one line per entry, and the lines are NOT all products. A real example:
           ["Reagents", "Extraction Buffer:", "100 mM Tris-HCl pH 8.2", "1.4 M NaCl"]
           That is a section header, a recipe title, and two buffer components --
           none of which is a thing you can buy.

    Feeding that straight into SKU matching produces garbage line items. This module
    is the §9 "role classify -> procurement filter" stage: label what each line
    actually IS, then keep only what a lab could place an order for.

FACTS ONLY (§10, PI-5)
    The extractor is instructed to output identifiers and quantities -- names,
    vendors, catalogue numbers, amounts -- and never to reproduce source prose.
    Facts are not copyrightable; the surrounding expression is. That distinction is
    what makes extraction legitimate even where redistribution of the text is not.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from astor.config import settings
from astor.protocols.schemas import RawProtocol

log = logging.getLogger(__name__)


class MaterialRole(str, Enum):
    """What a materials line actually is. The procurement filter keys on this."""

    KIT = "kit"                            # e.g. "RNeasy Mini Kit" -- a catalogue SKU
    REAGENT = "reagent"                    # e.g. "TRIzol Reagent"
    CONSUMABLE = "consumable"              # e.g. "1.5 mL Eppendorf tubes"
    EQUIPMENT = "equipment"                # e.g. "thermocycler" -- capital, not consumable
    BUFFER_COMPONENT = "buffer_component"  # e.g. "100 mM Tris-HCl pH 8.2" -- made, not bought
    NOT_A_MATERIAL = "not_a_material"      # e.g. "Reagents", "Extraction Buffer:" -- headers

    @property
    def purchasable(self) -> bool:
        """Can a lab place an order for this line as written?

        EQUIPMENT is excluded deliberately: it is real and buyable, but it is capital
        equipment rather than per-run consumable demand, so it does not belong in a
        protocol's bill of materials. BUFFER_COMPONENT is excluded because the lab
        makes the buffer -- though its components are often separately purchasable,
        which is a later resolution step, not this one.
        """
        return self in (MaterialRole.KIT, MaterialRole.REAGENT, MaterialRole.CONSUMABLE)


class ExtractedMaterial(BaseModel):
    """One materials line, classified. `vendor` + `catalog_no` are the payload:
    together they are a (brand, mpn) pair, which is exactly the Product dedupe key."""

    name: str = Field(description="The material as named, trimmed. No prose.")
    vendor: str | None = Field(default=None, description="Manufacturer/brand if stated.")
    catalog_no: str | None = Field(default=None, description="Catalogue/SKU number if stated.")
    amount: str | None = Field(default=None, description="Quantity as stated, e.g. '500 mL'.")
    role: MaterialRole = Field(description="What this line is.")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 in the classification.")


class ExtractedMaterials(BaseModel):
    """Wrapper so the model returns an object (JSON Schema needs a root object)."""

    materials: list[ExtractedMaterial] = Field(default_factory=list)


class MaterialExtractor(Protocol):
    def extract(self, raw: RawProtocol) -> list[ExtractedMaterial]: ...


# --------------------------------------------------------------------------- #
# Structured path -- no LLM
# --------------------------------------------------------------------------- #
def _looks_like_kit(name: str) -> bool:
    return "kit" in name.lower()


class StructuredMaterialExtractor:
    """For protocols whose materials already carry vendor + catalogue number.

    No LLM call: the source has done the structuring, so inventing a probabilistic
    step here would only add cost and a chance of corrupting good data. Role is
    assigned by a deliberately dumb rule, because a line that already has a vendor
    and a catalogue number is a purchasable product almost by definition -- the
    kit/reagent split is cosmetic and does not affect the procurement filter.
    """

    def extract(self, raw: RawProtocol) -> list[ExtractedMaterial]:
        return [
            ExtractedMaterial(
                name=m.name,
                vendor=m.vendor,
                catalog_no=m.catalog_no,
                amount=m.amount,
                role=MaterialRole.KIT if _looks_like_kit(m.name) else MaterialRole.REAGENT,
                confidence=1.0,
            )
            for m in raw.materials
            if m.vendor or m.catalog_no
        ]


# --------------------------------------------------------------------------- #
# LLM path -- for the free-text case
# --------------------------------------------------------------------------- #
SYSTEM = """You extract procurement facts from laboratory protocol materials lists.

You are given a protocol title, its materials lines, and some of its steps for \
context. The materials lines come from free text and are NOT all products. Classify \
every line into exactly one role:

  kit               a purchasable catalogue kit (e.g. "RNeasy Mini Kit")
  reagent           a purchasable chemical or biological reagent (e.g. "TRIzol Reagent")
  consumable        purchasable labware used up per run (e.g. "1.5 mL Eppendorf tubes")
  equipment         durable instrument, not consumed (e.g. "thermocycler", "centrifuge")
  buffer_component  a constituent of a buffer the lab prepares, typically written as a
                    concentration (e.g. "100 mM Tris-HCl pH 8.2", "1.4 M NaCl")
  not_a_material    a section header, recipe title, or prose fragment that names no
                    material at all (e.g. "Reagents", "Extraction Buffer:", "Note:")

Rules:
- Output one entry per input line. Do not merge, split, invent, or drop lines.
- Extract `vendor` and `catalog_no` ONLY if explicitly present. Never guess a vendor \
from the product name, and never infer a catalogue number.
- `amount` is the quantity as literally stated, or null.
- Copy identifiers and quantities only. Do NOT reproduce descriptive prose, protocol \
instructions, or any sentence from the source.
- Set `confidence` below 0.5 when a line is ambiguous."""


class LLMMaterialExtractor:
    """For protocols whose materials arrived as unstructured lines.

    Emits the SAME DTO as the structured path, so the procurement filter and
    everything downstream cannot tell which path produced a given record.
    """

    def __init__(self, model: str = "claude-opus-4-8", max_steps: int = 12) -> None:
        self.model = model
        self.max_steps = max_steps

    def _prompt(self, raw: RawProtocol) -> str:
        lines = "\n".join(f"- {m.name}" for m in raw.materials)
        steps = "\n".join(
            f"{s.number}. {s.text}" for s in raw.steps[: self.max_steps]
        )
        return (
            f"PROTOCOL TITLE\n{raw.title}\n\n"
            f"MATERIALS LINES (classify every one)\n{lines}\n\n"
            f"STEPS (context only -- do not extract materials from here)\n{steps}"
        )

    def extract(self, raw: RawProtocol) -> list[ExtractedMaterial]:
        if not raw.materials:
            return []
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "LLMMaterialExtractor needs ANTHROPIC_API_KEY. Use "
                "StructuredMaterialExtractor when the source already supplies "
                "vendor/catalogue fields."
            )
        from anthropic import Anthropic

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            # Adaptive thinking: the kit-vs-buffer-component call is a judgement, not a
            # lookup, and misclassifying a buffer component as a product puts a
            # non-orderable line into a customer's basket.
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": self._prompt(raw)}],
            output_format=ExtractedMaterials,
        )
        if response.stop_reason == "refusal":
            log.warning("extraction refused for %s/%s", raw.source, raw.source_id)
            return []
        return response.parsed_output.materials


def for_protocol(raw: RawProtocol) -> MaterialExtractor:
    """Pick the cheapest extractor that can do the job.

    If any material already carries a vendor or catalogue number, the source gave us
    structured data and the LLM would be pure cost. Otherwise we are looking at
    free-text lines and need the classifier.
    """
    if any(m.vendor or m.catalog_no for m in raw.materials):
        return StructuredMaterialExtractor()
    return LLMMaterialExtractor()


# --------------------------------------------------------------------------- #
# Procurement filter (§9)
# --------------------------------------------------------------------------- #
def procurement_filter(
    materials: list[ExtractedMaterial], min_confidence: float = 0.5
) -> list[ExtractedMaterial]:
    """Keep only lines a lab could actually order.

    Low-confidence lines are dropped rather than passed through: a wrong line item in
    a quote costs more than a missing one, because someone has to notice it is wrong.
    """
    return [
        m for m in materials
        if m.role.purchasable and m.confidence >= min_confidence
    ]
