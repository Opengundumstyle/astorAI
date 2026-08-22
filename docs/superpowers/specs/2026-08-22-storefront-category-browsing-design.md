# Storefront category browsing — Design

**Date:** 2026-08-22
**Status:** Draft — awaiting review of the curated allowlist (§4) and the inhibitors decision (§4.1)
**Scope:** Give the storefront assistant a shopper-legible answer to "what do you sell?", sourced
from the store's existing curated Shopify collections rather than an invented taxonomy, behind a
fail-closed allowlist. Adds real collection membership so browsing returns what is actually in a
collection, not a keyword guess.

## Problem

Asked "what categories does the store have?", the assistant says it has no browse tool and asks the
customer what they are working on. It is being honest — it has seven tools and none of them browse
— but "what do you sell?" is the most natural opening question a shopper has, and deflecting it is
a bad first impression for a catalog of 16,019 products.

The obvious fix is wrong. The `products.category` column is Shopify's `productType`
(`shopify_source.py:175`), and it is uncurated: 92 distinct values including the typo
`lab_consumbles` (519 products), the opaque `1_red` (72), `uncategorized` (100), packaging
descriptors like `6oz_square_bottom_bottles_combo` (4), and `bioleader_products` (8) — a supplier
name. Exposing those to shoppers would be worse than the current deflection.

## Key facts (verified 2026-08-22)

- The live store `astorscientific.us` has **47 curated Shopify collections** with human-written
  titles and real `products_count` values, already used for storefront navigation and SEO.
- The ingest ignores them: `_PRODUCTS_QUERY` in `src/astor/catalog/shopify_source.py` reads
  `productType` and `tags`, never `collections`.
- Shopify's `Product` type exposes a `collections` connection, so capturing membership is a small
  addition to the existing paginated query.
- Not all 47 are shopper-facing. `pricing-error-products` holds **143 products**;
  `sarsted-products` (522) and `nichiryo-pipettes-and-accessories` (15) and `3helix` (4) are
  supplier/brand names, which conflict with the confidentiality rule shipped in `3a9fadd`.
- Alembic history is diverged (the live schema was materialised via `Base.metadata.create_all`), so
  this design adds a **new table** rather than altering an existing one.

## Design

### 1. Ingest collection membership

Add to `_PRODUCTS_QUERY` in `src/astor/catalog/shopify_source.py`:

```graphql
collections(first: 20) { edges { node { handle title } } }
```

Parse into the product record as a list of `{handle, title}`. No other ingest behaviour changes.

### 2. New table `product_collections`

| column | type | notes |
|---|---|---|
| `product_id` | FK → `products.id`, cascade delete | |
| `handle` | text | Shopify collection handle — the stable key |
| `title` | text | snapshot of the Shopify title, for debugging drift |

PK `(product_id, handle)`; index on `handle`. A new table specifically to avoid an `ALTER` against
the diverged alembic history — `create_all` handles a new table cleanly.

### 3. Curated allowlist — `contracts/storefront-categories.v1.yaml`

Follows the existing `contracts/` convention. **Fail-closed: a collection absent from this file is
invisible to the assistant.** This is the control that keeps `Pricing Error Products` out without
anyone having to remember it exists, and it makes "what does Astor sell?" a merchandising decision
the team owns rather than a query result.

```yaml
version: 1
categories:
  - handle: recombinant-proteins
    display: Recombinant Proteins
    flagship: true
```

`display` overrides the Shopify title, which is sometimes an SEO landing-page string rather than a
category name. `flagship` marks the areas the assistant leads with.

### 4. Drafted allowlist — **REVIEW THIS**

16 of 47, ordered by size. Counts are live `products_count` values; products can appear in more
than one collection.

| handle | display | count | flagship |
|---|---|---|---|
| `recombinant-proteins` | Recombinant Proteins | 9,207 | ✅ |
| `common-lab-reagents` | Lab Reagents | 2,796 | |
| `lab-supplies` | Lab Supplies | 2,341 | |
| `plasticware` | Plasticware | 618 | |
| `pcr-reagents` | PCR Reagents | 276 | |
| `proteins` | Proteins & Antibodies | 177 | |
| `biobanks` | Biobanking & Sample Storage | 122 | |
| `pipette-tips` | Pipette Tips | 122 | |
| `sample-collection` | Sample Collection | 106 | |
| `elisa-kits` | ELISA Kits | 104 | |
| `cell-tissue-culture` | Cell & Tissue Culture | 83 | |
| `liquid-handling` | Liquid Handling | 70 | |
| `cell-biology-reagents` | Cell Biology Reagents | 62 | |
| `pcr-plasticware` | PCR Plasticware | 41 | |
| `equipment` | Equipment | 17 | |
| `microbiology` | Microbiology | 12 | |

Two `display` values deliberately rewrite SEO titles: `lab-supplies` ("Premium Lab Supplies for
Accurate and Safe Scientific Workflows") and `proteins` ("Proteins & Antibodies (Premium
Research-Grade Reagents)").

**Excluded, and why:**

- **Internal:** `pricing-error-products` (143), `lowest-price` (9), `clearance-sales` (2). The first
  is the reason this file is an allowlist rather than a denylist.
- **Brand/supplier names:** `sarsted-products` (522), `nichiryo-pipettes-and-accessories` (15),
  `3helix` (4). Surfacing these would undo `3a9fadd`. Their products remain reachable through
  `plasticware`, `liquid-handling`, etc.
- **Junk or broken:** `other-products` (3), `filteration` (0 products, and misspelled),
  `biofactory` (3).
- **Too granular for a top-level list** — reachable by search, and folding them in would make the
  browse list unreadable: the four sample-collection sub-collections (blood 104, urine 11, saliva 4,
  fecal 6, all under `sample-collection`), plus `cover-glasses`, `cuvettes`, `markers`,
  `petri-dishes`, `adhesive-film`, `centrifuges`, `biosafety-cabinet`, `elisa-plasticware`,
  `deep-well-plates-and-accessories`, `cell-culture-chamber-slides`, `centrifuge-tubes`,
  `inoculation-spreader-and-needles`, `vaccum-filtration`, `endotoxin-detection-reagents-and-kits`,
  `bacterial-culture-reagent`, `sample-preparation-kit`, `cell-culture-plates-and-flasks`.

### 4.1 Open decision: biochemical inhibitors have no collection — **NEEDS A DECISION**

The agreed framing is to lead with recombinant proteins **and biochemical inhibitors**. Proteins are
the largest collection at 9,207. But `biochemical_inhibitors` is a `productType` with **2,046
products and no corresponding Shopify collection**. It is the second-largest area of the catalog and
cannot be offered through collections as they stand.

Three ways out, for the reviewer to pick:

1. **Create the collection in Shopify** (recommended). Fixes it at the source, benefits storefront
   navigation and SEO as well as the assistant, and keeps this design's single-source rule intact.
   Requires merchandising work in the Shopify admin.
2. **Allow a `productType` fallback entry** in the YAML (`source: product_type`, value
   `biochemical_inhibitors`). Ships without Shopify work, but introduces a second taxonomy source
   and weakens the "collections are the taxonomy" rule.
3. **Ship with proteins as the only flagship** and treat inhibitors as a follow-up. Honest, but the
   opening answer then under-sells roughly an eighth of the catalog.

Option 1 is the only one that leaves the system with one taxonomy.

### 5. Tools

**`browse_categories()`** → the allowlist intersected with live counts from `product_collections`,
flagship entries first, then the rest by size. Returns `{"categories": [{"name", "count",
"flagship"}]}`. No arguments — it is the "what do you sell" entry point.

**`category_products(category, limit=8)`** → members of one collection, resolved from the `display`
name the model saw (case-insensitive; handles accepted too). Product payloads go through
`roles.gate_product(..., roles.BUYER)`, same as every other product tool.

### 6. Prompt

Add to `SYSTEM`: a question about what Astor carries, or any request to browse, calls
`browse_categories`. Lead with the flagship areas and their counts, name at most a few others, and
offer to go deeper — never dump all 16 as a list. This implements the agreed "showcase blended into
orient" framing: a specialist supplier reads as a specialist, not a generic reseller.

### 7. Cleanup: stop exposing raw `productType` to buyers

Remove `"category"` from `roles._PRODUCT_BUYER_KEYS` and `_DETAIL_BUYER_KEYS`. Once collections are
the shopper-facing taxonomy, the raw `productType` value has no reason to reach a buyer — and it is
currently the path by which `bioleader_products` (a supplier name) is buyer-visible. The
`/api/products?category=` filter is unaffected; only the response field is withheld.

### 8. Error handling

- Allowlisted handle absent from Shopify, or zero live members → omitted from `browse_categories`
  and logged at WARNING. A stale YAML entry degrades quietly instead of breaking the turn.
- Model passes an unrecognised category → `{"error": "unknown category", "valid": [...]}`, matching
  the existing recoverable tool-error convention in `tools.py`.
- Ingest returns no collections for a product (uncollected) → no rows; the product stays searchable
  and simply is not browsable.

### 9. Testing

- **Fail-closed test (the important one):** seed `product_collections` with
  `pricing-error-products` and assert it never appears in `browse_categories` output.
- Extend the existing recursive confidentiality guard in `tests/test_chat_tools.py` to cover both
  new tools, so `category_products` cannot leak `brand`.
- Allowlist parses; handles unique and non-empty; every entry has a `display`.
- `browse_categories` orders flagship first, then by count; omits allowlisted-but-empty collections.
- `category_products` resolves by display name case-insensitively; unknown name returns the
  recoverable error with the valid list.
- Ingest: a product node with two collections yields two `product_collections` rows.
- `roles` gate: `category` absent from both buyer payloads.

### 10. Rollout

The ingest must be re-run against the Render database to populate `product_collections`, which means
temporarily re-opening the IP allowlist per `docs/render-runbook.md`. Until it is populated,
`browse_categories` returns an empty list and the assistant behaves as it does today — so the code
can deploy before the data lands.

## Non-goals

- Cleaning up the 92 `productType` values in Shopify.
- Metafield-driven curation (marking collections browsable in Shopify itself) — the intended end
  state, deferred until the allowlist proves stable.
- Faceted or multi-select browsing; sub-collection drill-down.
- Any change to how `search_products` ranks.
