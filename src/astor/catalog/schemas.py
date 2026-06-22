"""Pydantic DTOs that flow through the ingestion pipeline."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedProduct(BaseModel):
    """Raw-ish output of an Extractor, before normalization."""

    supplier_sku: str
    name: str
    category: str | None = None
    brand: str | None = None
    mpn: str | None = None
    pack_size: str | None = None
    cost: float | None = None
    currency: str = "CNY"
    stock: int | None = None
    lead_time_days: int | None = None
    specs: dict = Field(default_factory=dict)


class NormalizedProduct(BaseModel):
    category: str
    name: str
    brand: str | None = None
    mpn: str | None = None
    specs: dict = Field(default_factory=dict)


class NormalizedOffer(BaseModel):
    supplier_sku: str
    pack_size: str | None = None
    cost: float
    currency: str = "CNY"
    stock: int | None = None
    lead_time_days: int | None = None


class NormalizedItem(BaseModel):
    product: NormalizedProduct
    offer: NormalizedOffer
