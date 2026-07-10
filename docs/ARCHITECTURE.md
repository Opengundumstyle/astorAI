# Astor Scientific — Architecture & Strategy Context

> **Purpose of this file.** Self-contained context for the `astorAI` repo. It captures
> the strategy, the locked architectural decisions, the neutral engine contract, the
> protocol→BoM layer, the data/curation strategy, the Shopify integration, the cart &
> one-tap UX, testing, and the open decisions with ownership. Feed it to the repo's
> assistant and read it yourself. It complements the three contract artifacts:
> `astor-engine-contract.v1.yaml`, `astor-protocol-bom.v1.yaml`, `astor-protocol-ingest.v1.yaml`.
>
> **Reading guide.** "Locked" = decided, build against it. "Open" = needs a decision
> (see §14 for who owns each). "Corrected" flags a belief that changed during design.

---

## 1. What Astor is (in three sentences)

Astor is a **merchant-of-record distributor with an AI agent layer on top** — not an AI
company. The agent turns an experiment into a grounded, buyable shopping list; Astor
sources and fulfills as the distributor; the customer keeps final approval on every order.
Defensibility comes from **traceable evidence + human control + per-category completeness
checklists**, not from a smarter model.

Positioning:
- To customers: *your procurement agent.*
- To suppliers: *your AI distribution channel.*
- Operating principle: **AI assists, Astor fulfills, the customer approves.**

## 2. Market position (why the opening exists)

The field splits into two groups that rarely overlap:
- **Distributors** (Quartzy, classic resellers): real fulfillment & merchant-of-record, but
  no shipped conversational AI layer.
- **AI / marketplaces** (ZAGENO, Scientist.com, Biocompare LifeSciAI, Bioz): smart discovery
  and evidence grounding, but not the merchant-of-record distributor.

Astor = a genuine distributor **+** a genuine AI agent layer. That combination is largely
uncontested today. (This is a claim about competitors as of mid-2026; it can erode if a
distributor ships conversational AI or a marketplace becomes merchant-of-record.)

Avoid direct collision with: Labviva (enterprise ERP procurement integration),
Scientist.com (complex R&D service / SOW orchestration), BioMall.ai (SKU marketplace).
Astor's lane: mid-size biotech, new labs, CROs, small pharma, academic groups — PO-native
product/consumable/reagent procurement with distributor fulfillment.

## 3. Core principles (the intellectual spine)

1. **Credibility = traceable evidence + calibrated confidence + human control** — *not*
   model IQ. In a wet lab, a confident-but-wrong recommendation costs weeks and money; the
   failure is asymmetric, so answers must be falsifiable/checkable (citations, confidence
   scores), not authoritative-sounding.
2. **Copilot, not autopilot.** Field consensus (and the credible players) keep humans on the
   commercial decision. Full autonomous supplier selection / auto-PO hits a trust wall.
3. **Three separate trust mechanisms — never conflated:**
   - *Science* (why a protocol needs an item) → **academic citations**.
   - *Fit* (which SKU satisfies a line) → **equivalence + provenance** (Astor's judgment, cited).
   - *Commerce* (price / stock / lead-time) → **transparent commercial fact**, never an academic claim.
   "Everything academically sourced" is only literally true for the science. Don't overclaim it.
4. **No multi-agent swarm.** Decentralized reasoning swarms amplify errors, fail at high rates
   in production, produce false consensus, and destroy traceability. Credibility here comes
   from grounded retrieval, not reasoning horsepower. Use **one** genuine agent + deterministic
   surrounding workflow. (Reference evidence: Anthropic "Building Effective Agents"; OpenAI
   "A Practical Guide to Building Agents"; FutureHouse PaperQA2; Biomni.)

## 4. The four-plane architecture

Four planes. Planes 1–3 are the **engine** (platform-agnostic). Plane 4 is a **swappable
adapter** (Shopify today). Only Plane 2 is an agent; everything else is deterministic and
unit-testable without an LLM.

- **Plane 1 — Conversation & intent** *(workflow; Anthropic chaining/routing)*
  Requirement-elicitation state machine: intent → slot extraction → clarification loop until
  a completeness threshold is met → then hand off. NOT an agent. "Understand before
  recommending" is enforced here.
- **Plane 2 — Knowledge & evidence** *(THE one agent; agentic RAG, PaperQA2 pattern)*
  Two **separate** retrieval targets, never merged:
  - *Catalog grounding* → which SKU satisfies a line (equivalence matcher + pgvector).
  - *Scientific grounding* → why this item, with citation (PaperQA2-style literature RAG,
    metadata-aware: citation count, journal rank, retraction status).
  External academic sources feed **only** the scientific half.
- **Plane 3 — Fulfillment routing** *(workflow; deterministic classifier)*
  Each required line routes to one of three lanes: in-stock (one-click add) / known-but-not-
  stocked (request-for-quote → sourcing) / not-available (external redirect link, **logged**
  as a sourcing-pipeline signal).
- **Plane 4 — Commerce adapter** *(deterministic; Shopify today, swappable)*
  Draft order, checkout, payment (PCI), net terms, order-of-record.

**Human approval gate** sits on the seam between Plane 3 and Plane 4: buyer confirmation is
the gate (OpenAI's "human approval before purchases" pattern).

**The thin waist.** Engine ↔ adapter cross at exactly **two points**: the draft at confirm
(engine→adapter) and `order.paid` coming back (adapter→engine). If a third or fourth crossing
appears, the boundary is leaking.

**Cost/latency reality (design around it).** PaperQA2-style agentic retrieval can run 1–5 min
and thousands of tokens. So the protocol→BoM mapping is **precomputed and cached** per protocol;
a live chat turn is a fast template lookup + cached per-line grounding, not a cold agentic loop.

**Academic-grounding caveat.** Literature-RAG fails outside academic corpora (metadata providers
won't find non-academic docs). This is a second reason scientific grounding and catalog grounding
must be separate retrieval systems with separate trust mechanisms.

## 5. Locked consensus — engine/adapter boundary

These are settled; build against them:

1. **Cart lives in the engine** — conversational cart is engine state, never a Shopify cart.
2. **Order = a Shopify Draft Order**, materialized only at buyer confirmation.
3. **Shopify owns the money** — payment (PCI), net terms, order-of-record.
4. **Upstream merchant-of-record PO fires on `order.paid`** (engine re-enters here).
5. **Zero Shopify types in the engine** — all coupling in one swappable adapter.
6. **Buyer confirmation is the human approval gate.**

**Handoff decision (locked):** at payment, the transaction hands off to Shopify checkout /
invoice-from-draft. Shopify earns its place precisely by owning payment + net terms +
order-of-record. (If the agent/engine ever owned payment too, Shopify would be doing nothing
but holding a product table and should be dropped — but that is NOT the chosen path.)

## 6. Cart & one-tap purchase UX (important nuance)

**"Cart lives in the engine" is about storage, not presentation.** The chat UI (chat bubbles,
product cards, "Add to cart", subtotal, quantity steppers, checkout) looks like Amazon/Alexa
for-shopping. The architectural fact is invisible to the user: the cart list is stored in
**Astor's engine**, not Shopify's cart object.

- Tapping "Add to cart" in chat → appends to the engine's working order (next to conversation,
  equivalence match, landed cost, citation) so the agent can reason over the whole order
  (completeness, missing controls, cost).
- The cart the customer sees **IS** the real cart — not a preview later copied into Shopify.
- Shopify learns about the cart only at **checkout**, when the engine hands it over as a draft.
- The engine renders the cart view from its own data (small front-end cost you own) — you do
  NOT get Shopify's cart UI "for free". That's the price of the agent reasoning over the order.

**Why engine-cart fits B2B specifically.** Lab carts aren't fleeting B2C sessions: a scientist
builds it, a PI approves, procurement issues a PO days later on net terms. The engine cart must
**persist, be named, and be shareable** ("my Western blot order"). Shopify's cart is a
short-lived B2C session object — wrong for this. Engine cart is a first-class saved object.

**One-tap "Buy now" in chat.** Achievable for **returning** customers with credentials on file;
a **first-ever** purchase can't be truly one-tap for anyone (payment/PCI reality — even Amazon's
first order wasn't). One-tap "buy" = the approval gate collapsed to a single tap for someone
already set up. Behind the tap, engine materializes the draft and completes it against stored
credentials; Shopify still executes payment/terms.

**For labs requiring PO number or PI sign-off:** "Buy now" resolves to **one-tap
submit-for-approval**, not one-tap charge. Matching their procurement rule is a feature.

## 7. Neutral engine contract (see `astor-engine-contract.v1.yaml`)

Transport-agnostic. JSON Schema payloads so a validator at the seam rejects non-conforming
(and Shopify-shaped) messages.

**Channels:** SYNC endpoints (request/response, via App Proxy) + ASYNC events (one-way, any bus).

**Sync endpoints (engine-facing):**
- `POST /v1/discover` — conversational discovery + equivalence + evidence-grounded ranking.
- `POST /v1/quote` — create/mutate the engine-side working order (the cart). Never touches Shopify.
- `POST /v1/confirm` — buyer confirms; engine freezes order, mints `ord_`, emits `draft.proposed`.
- `POST /v1/exceptions/{id}/resolve` — buyer resolves an exception; may emit `order.amended`.

**Events:**
- engine→adapter: `catalog.upsert`, `catalog.retract`, `draft.proposed`, `order.amended`, `exception.raised`
- adapter→engine: `order.paid` (engine cuts upstream PO), `order.cancelled`

**Envelope:** `schema_version, event_type, event_id, occurred_at, correlation_id, source(engine|adapter — never "shopify"), idempotency_key, payload`.

**Key invariants:**
- INV-1: reject any message matching `gid://shopify/` or Shopify-shaped keys (boundary enforced by validator).
- INV-2: engine mints IDs; adapter resolves Shopify GIDs → engine IDs before emitting.
- INV-3: at-least-once delivery → consumers idempotent on `idempotency_key`.
- INV-4: unordered delivery → key on entity id + state machine, drop stale via `occurred_at`.
- INV-5: money as integer minor units + ISO-4217 (no floats).
- INV-6: engine imports zero adapter SDKs.

**ID conventions:** prefixed ULIDs — `prod_ off_ co_ buy_ ord_ exc_ qte_ evt_ corr_`.

**Open in the contract:** can an exception fire *after* `order.paid`? Currently `order.amended`
is pre-payment (draft edit) only. If upstream stockouts can surface post-payment, a
refund/partial-fulfillment path is needed (Shopify B2B post-purchase editing is weak). → §14 #3.

## 8. Protocol→BoM layer (see `astor-protocol-bom.v1.yaml`)

The net-new module at the top of Plane 2. Turns "an experiment" into "the list of things to
source" **before** grounding runs. This is a different asset from the equivalence matcher
(which turns "a required thing" into "a purchasable SKU").

**Flow:** two entry modes (named protocol/paper → parse; goal → map to template) → elicitation
slot-fill loop (`↻` re-ask until complete) → protocol resolution → **BoM derivation** → per-line
fan-out to catalog + scientific grounding → `discover`/`quote`.

**The asset is the ProtocolTemplate library** (offline, curated, versioned). At request time the
LLM **routes** (messy input → `proto_id`) and **extracts** (paper → structured steps); it does
**NOT** author BoM lines. Required items come from the template, deterministically.

**Key entities:** `Slot`, `ProtocolTemplate` (with `bom_template` lines), `ElicitationState`
(gathering|ambiguous|ready, `next_questions`, `gate_passed`), `BillOfMaterials`, `BomLine`,
`CompletenessReport` (gap detection — the differentiator).

**Invariants:**
- PB-1: no BoM until the elicitation gate passes (completeness threshold).
- PB-2: BoM instantiation does no live literature retrieval / no LLM authoring of line contents.
- PB-3: every line carries ≥1 academic Evidence.
- PB-4: rationale.evidence describes SCIENCE only — never carries price/stock/lead-time.
- PB-5: the LLM may resolve/extract, may NOT author templates at request time (offline pipeline only).
- PB-6: neutral (inherits INV-1).

**Precompute & cache:** template library built offline; per-line rationale attached offline;
instantiated BoM cached under `hash(protocol_id, version, normalized required slots)`; per-product
scientific grounding cached per product on its own cadence.

**Open:** `ambiguous` resolution UX — when two `proto_id` score close (e.g., native vs.
crosslinking co-IP), ask a disambiguating question, or derive both and let the buyer pick? → §14 #4.

## 9. Offline protocol-ingest pipeline (see `astor-protocol-ingest.v1.yaml`)

Produces `ProtocolTemplate`. Built on a **source-adapter boundary** (protocols.io today;
PMC/manual tomorrow) — pipeline consumes only the neutral `RawProtocol`.

**Stages** (automation: auto|assist|human): 1 normalize (adapter) → 2 resolve category & dedupe
→ 3 classify role → 4 procurement filter (drop lab-owned commons) → 5 derive spec (→ catalog
grounding) → 6 derive quantity rule (scale-to-buy) → 7 augment completeness (inject missing
controls from checklist) → 8 bind attribution → 9 type requirements → 10 validate → 11 human
review & publish.

**Curated assets you maintain** (amortize across all protocols): `role_taxonomy`,
`procurement_filter_rules`, and — highest value — **`category_completeness_checklists`** (per
category, the required roles a correct order MUST contain, incl. controls). The checklist is the
moat and the gap-detection ground truth; it's the one thing no source hands you.

**Labor ledger (protocols.io as input):**
- *Eliminated:* prose→structure parsing; the materials list existing at all; per-line citation.
- *Reduced:* category resolution; quantity ratios; some spec signal.
- *Still fully yours:* role classification; procurement filter; materials→SKU spec; scale-to-buy
  quantity; completeness augmentation; requirement typing; human review.
- *Verdict:* protocols.io removes the parsing tax, not the materials→procurement-BoM transform —
  and that transform IS the product. Keep a PMC/manual fallback adapter for coverage gaps.

## 10. Data & curation strategy

**The legal lever:** a protocol's steps and materials are **facts — not copyrightable** (like a
recipe's ingredient list). Extract facts, restructure into your schema, attribute; never copy
prose or figures. Three distinct legal layers: facts (free) / expression = prose+figures
(copyrighted, avoid) / a provider's database & ToS (contract-restricted, don't bulk-scrape).

**Sources:**
- **PMC Open Access / Europe PMC** — mostly CC-BY, machine-readable JATS XML, text-mining
  permitted, already in reach. *Free path — you own all the parsing.*
- **protocols.io** — structured steps + API; many CC-BY user protocols; platform ToS restricts
  bulk/commercial harvest (fee/licence). *Fast path — skips parsing, not the procurement transform;
  coverage varies.*
- **Manufacturer protocols** (Thermo, NEB, Bio-Rad, Abcam) — free, copyrighted prose, map to
  specific products. *Best product-mapping signal, but vendor-biased — de-bias.*
- **Bio-protocol** — OA but often **CC BY-NC** (blocks commercial reuse of expression). Extract
  facts + paraphrase only.
- **Nature Protocols / CSH / JoVE** — subscription, all-rights-reserved. Avoid as systematic sources.

**Cost truth:** the bottleneck is **curation labor, not licence fees** (if you stay on the
free-facts path). The `license` tag travels with each derived template (INV `PI-2`) so
redistribution stays clean per-line across mixed sources.

**Buy-vs-build:** value a protocols.io commercial licence against only what it *reduces* (parsing
tax + citation) — not against the transform you build regardless. If the fee < parsing-pipeline
cost for your covered categories, take it + keep a PMC fallback; else use the free PMC path.
**Legal caveat:** confirm facts-vs-expression with IP counsel before productionizing at scale.

## 11. Shopify integration specifics (adapter-local)

All of this lives in the adapter, never the engine.

- **App Proxy** — storefront/agent calls to the external engine under the shop domain, no
  credential exposure. Carries the sync endpoints (`discover`/`quote`/`confirm`/`resolve`).
- **Webhooks** (`orders/paid`, `orders/create`, etc.) — 5s ack timeout, HTTP 200 required,
  up to 8 retries over 4h then subscription deleted. Adapter must: ack fast + process async,
  verify HMAC, be idempotent (no duplicate upstream PO).
- **Admin GraphQL API** — required for new apps (REST legacy). Writes products + B2B price lists.
- **Draft Orders** (+ invoice-from-draft) — the quote→order primitive; draft→complete maps onto
  the approval gate. On all paid plans.
- **Customer Account UI extensions** — surface exceptions/substitutions to the buyer natively.
- **B2B (as of Apr 2026):** company accounts, up to 3 custom catalogs, net terms (Net 30/60/90),
  PO numbers, self-serve on all paid plans. **Plus** adds unlimited catalogs, dedicated B2B
  storefront, **checkout extensibility via Shopify Functions**, EDI, vaulted cards.
- **Constraint:** checkout-moment logic (real-time landed cost, credit checks, dynamic pricing)
  needs checkout extensibility = **Plus-only** (~$2,300/mo). Astor's differentiators live
  pre-cart and post-order, so this is survivable — unless one-tap/stored-card/vaulting pushes
  you to Plus (→ §14 #6). Post-purchase B2B order editing is weak → handle substitution
  pre-fulfillment via Customer Account UI + new/edited draft, not checkout edits.
- **Data-model discipline:** mirror only what the buyer sees (product, price, order, display
  metafield) into Shopify. Supplier offers, equivalence, landed-cost breakdown stay in Postgres.
  Shopify is the commerce layer synced with the back-office, rarely the system of truth.

## 12. Testing strategy (engine-first)

Test in dependency order; the differentiated risk is engine-side, so test it first — **without
Shopify**:
1. **Engine vs contract** — validate `discover`/`quote`/`exception` against JSON Schema with a
   fake adapter; use the existing eval harness (precision/recall/F1, threshold sweep). No Shopify.
2. **Adapter in isolation** — mock the engine; test webhook normalization, HMAC, ID-mapping,
   idempotency, the 5s ack; replay saved Shopify webhook payloads.
3. **Consumer-driven contract tests across the seam** (Pact-style) — guarantees agnosticism holds
   as both sides evolve.
4. **Integrate on a Shopify *dev store*** — a free sandbox (NOT a live website). Tunnel App Proxy
   → local engine; round-trip draft → checkout → `order.paid`.
5. **Thin storefront last** — minimal theme + chat widget. Cheapest, most disposable layer.

You need a **dev store, not a live site**. GarboBio's real protocols/orders = gold data (the
completeness answer key no synthetic test gives).

## 13. What's built vs. net-new

**Built** (committed to `github.com/Opengundumstyle/astorAI`, through M1–M2):
equivalence matcher (pgvector HNSW ANN + attribute-rule scoring), catalog ingestion pipeline
(schema migration, LLM extraction), landed-cost engine, embedding providers (Voyage/OpenAI,
batched), evaluation harness.

**Net-new:** protocol→BoM layer (elicitation, derivation, grounding); per-category completeness
checklists (**the moat**); offline protocol-ingest pipeline; the neutral engine contract as code;
the Shopify adapter; the engine-side cart rendering.

**The moat is the completeness checklist — not the model.**

## 14. Open decisions + ownership (who must say yes)

Most of the architecture is **Zhile's alone** to decide (owns all technical decisions) — just
inform co-founders. Only a few items need a hard consensus. Tagged below.

1. **protocols.io licence — cost & terms.** Buy the fast path or build on free PMC? *(spend
   decision — co-founder/partner input)*
2. **Completeness checklists for first 3–5 categories.** The moat; biology domain truth.
   **HARD CONSENSUS with Mary (于雪)** — Zhile is not the domain expert. *This is the meeting.*
3. **Post-payment exceptions.** Can a stockout hit after `order.paid`? Decides whether a
   refund/partial-fulfillment path is needed. *(Zhile's technical call, but customer-trust /
   sourcing-partner dimension.)*
4. **Ambiguous protocol resolution UX.** Ask a disambiguating question vs. derive both. *(Zhile /
   UX.)*
5. **Elicitation thresholds per category** — required vs. optional slots. *(Zhile / needs Mary's
   domain input on what's required.)*
6. **Shopify Plus?** Checkout extensibility + vaulting/stored-card lean on Plus. Justify Shopify
   by commerce rails, not chat. *(spend/commitment.)*
7. **Autonomy vs. approval gates.** Horace prefers fewer gates; the credibility thesis leans on
   the confirm gate. **HARD CONSENSUS with Horace** — settle as a risk-tiered principle, don't
   decide unilaterally.
8. **First-order payment setup: net terms only / stored card / both.** Shapes the entire
   returning-customer one-tap experience. Depends on whether first 10 customers are established
   labs (→ start net terms) or individual researchers/startups (→ start stored card). Converges
   on "both" over time. *(spend/commitment + partner input on credit risk.)*
9. **IP counsel** confirm facts-vs-expression before productionizing ingestion at scale.
   *(outside lawyer — not a co-founder decision.)*

**Consensus summary:** of the above, only **#2 (checklists, with Mary)** and **#7 (autonomy,
with Horace)** are true hard-consensus items with co-founders. The rest are Zhile's call,
spend approvals, or a lawyer's call.

## 15. GarboBio's role (CORRECTED)

GarboBio is a **validation partner and anchor customer — NOT an authoritative protocol source.**
Standard **published** protocols (protocols.io, Nature Protocols, manufacturer refs) are *more*
credible than any single lab's in-house method and are the base for the template library.
GarboBio's value is different and real: their actual lab orders are **ground truth for
completeness** (a missing item in practice = a checklist entry) and free, aligned validation
data. Their real adaptations can be an *enrichment* layer marked "one lab's practice," never the
authoritative base.

- **Build the template library on standard published sources** (for credibility + coverage).
- **Use GarboBio for completeness validation and real-world signal**, not as source of truth.

## 16. Reference architectures (for design decisions)

- **Anthropic, "Building Effective Agents":** workflows (predefined code paths) vs. agents
  (dynamic). Patterns: prompt chaining, routing, parallelization, orchestrator-workers,
  evaluator-optimizer. Start simple; don't reach for multi-agent. → Planes 1 & 3 = workflow.
- **OpenAI, "A Practical Guide to Building Agents":** single agent first; human-approval node
  before sensitive side effects (purchases); validation next to the tool with the side effect;
  retry limits + escalation. → the confirm gate; elicitation failure thresholds.
- **FutureHouse PaperQA2** (open source `Future-House/paper-qa`): agentic RAG, single agent +
  tools; three-phase (query → retrieve+chunk+embed with metadata → rerank/contextual-summarize →
  grounded inline-cited answer); beats PhD/postdoc on literature retrieval **by grounding, not
  reasoning**; fails outside academic corpora. → Plane 2 near-drop-in for scientific grounding.
- **Biomni:** agentic planner + curated tool/DB environment (tool-calling). → reference if the
  agent later *calls* OpenAlex/PubChem/CiteAb as live tools rather than pre-indexing.

## 17. Tools & data sources (confirmed usable)

Vector search: pgvector HNSW (ANN candidates). Embeddings: Voyage/OpenAI, batched.
Academic grounding (usable): OpenAlex, Europe PMC, PubMed, PubChem, RRID/SciCrunch,
Human Protein Atlas, Addgene. CiteAb: most decision-critical commercial source, requires licence.
Knowledge-arch references: PaperQA2, Anthropic/OpenAI agent guides, Biomni.
Env: Windows, Cursor, Git Bash. Repo: `github.com/Opengundumstyle/astorAI`.

---

*This document consolidates the architecture/strategy design session. Update it as the open
decisions in §14 are resolved.*
