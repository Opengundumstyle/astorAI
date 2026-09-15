# Biomni vs. the Astor assistant: what to borrow, what to leave

**Date:** 2026-09-13
**Status:** Recommendation. No code changes proposed in this doc.
**Reference clone:** `~/code/reference/Biomni` at commit `400c1f3` (read-only, never committed).
**Sources read:** `biomni/agent/a1.py`, `biomni/model/retriever.py`, `biomni/tool/tool_registry.py`, `biomni/know_how/loader.py`, `biomni/config.py`, `docs/configuration.md`. The auto-generated `DETAILS.md` is stale and was not relied on.

---

## The decision in three lines

1. **Do not rebuild the chat on Biomni's architecture.** Our native tool-use loop is already the right shape for a storefront. Biomni's code-execution loop would be a regression.
2. **Match Biomni's level in exactly two places:** a curated knowledge corpus with provenance metadata, and a benchmark that gates every change.
3. **Biomni is not a "troubleshooting brain" we lack.** Its troubleshooting is the base model plus two know-how documents. What we lack is grounded troubleshooting guides that end in a shopping list, and a grader for them.

---

## 1. What Biomni is

Biomni (Stanford SNAP) is a general biomedical research agent. One LLM, one loop, a large library of tools and datasets, and a Python/R/Bash sandbox.

**The core loop** is a two-node LangGraph state machine:

- `generate` calls the model with a large system prompt and parses the reply for one of three XML tags. `<execute>` routes to the execute node, `<solution>` ends the run, `<think>` alone loops back. Two malformed replies in a row terminate the run.
- `execute` runs the code block in a persistent Python REPL, or R or Bash when the block starts with a marker. Output is truncated at 10k characters and fed back as an `<observation>` message.
- Recursion limit is 500 steps. An optional self-critic node asks the same model to critique the transcript and loop again. Off by default.

**The key design choice:** the model never gets native tool calling. Every tool is a plain Python function. The agent uses a tool by writing an import and a call inside a code block. Tools, datasets, and libraries are all just things the model can import or read from disk.

**Keeping the prompt manageable.** The environment holds hundreds of tool functions, roughly a hundred data-lake files, dozens of libraries, and know-how documents. Before each run, a retriever pastes every description into a prompt as a numbered list and asks the model to return index lists per category. No embeddings. The system prompt is then regenerated with only the chosen items.

**Know-how documents** are markdown files with a `## Metadata` block carrying author, licence, version, and a commercial-use flag. A small loader parses them. A `commercial_mode` switch filters out any dataset or document not licensed for commercial use. There are two such documents today: sgRNA design and single-cell annotation.

**Protocols** are first-class. A `protocols.py` tool wraps the protocols.io search API, there are local protocol folders for Addgene and Thermo Fisher, and the system prompt carries an explicit instruction: include reagent and catalogue details only when found in a source, and prefer accuracy over completeness.

**Eval** ships as a package: task loaders, graders, and a benchmark with per-task reward functions.

## 2. What we have

`src/astor/chat/agent.py` is a bounded Claude tool-use loop: one model, one cached system prompt, seven structured tools, a cap of six iterations per turn, streaming and non-streaming variants, and an injectable client so the loop is testable with a fake.

The seven tools: `search_products`, `search_protocols`, `protocol_products`, `product_protocols`, `product_detail`, `protocols_by_material`, `flag_sourcing_request`.

The system prompt already opens two lanes. Catalog facts must come from a tool call this turn. Scientific knowledge, including troubleshooting and experimental design, may come from the model's own expertise. So the raw capability to reason about experiments is switched on today. What it lacks is grounding and a way to measure it.

## 3. Side by side

| Dimension | Biomni | Astor today | Verdict |
|---|---|---|---|
| Agent shape | One LLM, generate/execute loop | One LLM, tool-use loop | Same thesis. Keep ours. |
| Tool interface | Model writes code; tools are importable functions | Native structured tool calls | Keep ours. Code execution needs a sandbox and 10-minute timeouts; wrong for a storefront. |
| Iteration budget | 500 steps, optional critic rounds | 6 per turn | Keep ours. A chat turn that loops 50 times has already failed. |
| Retrieval | LLM picks indices from a numbered list | pgvector + equivalence matcher | Keep ours. Theirs does not scale to a 16k-item catalog and is non-deterministic. |
| Knowledge corpus | Tool descriptions, dataset descriptions, know-how with licence metadata | Checklists, roles, elicitation tables, protocols.io corpus | **Match their discipline.** Every entry needs source, licence, review date, commercial flag. |
| Grounding rules | "Only include catalogue details found in a source" | "Never invent a product, SKU, or protocol" | Same spirit. Borrow their protocol wording. |
| Eval | Task loaders + graders as a package | Equivalence gold set on a branch | **Match their level.** Promote to a standing benchmark. |
| Licensing | `commercial_mode` filters data and docs | Tracked manually for protocols.io | Borrow the pattern. |

## 4. Borrow these three things

**The know-how document convention.** Markdown files with a metadata block, parsed by a small loader, filtered by a commercial-use flag. This maps directly onto per-category completeness checklists and troubleshooting guides. It lets Mary's curation output feed the assistant without a code change per document, and it gives us licence tracking for free.

**Two-stage prompt assembly.** Biomni builds a base prompt once, then swaps in a query-specific resource section per run. Our cheaper version: keep the static system prompt cached, and prepend the one or two relevant checklists once intent is known. Choose them with a deterministic category classifier, not a model call, so the cache stays warm.

**The protocol-grounding instruction.** Include reagent and catalogue specifics only when a source contains them. Prefer accuracy over completeness. This belongs in our fit-and-commerce trust rules verbatim.

## 5. Leave these behind

- **Code execution as the tool interface.** Their tasks need pandas and R on user data. A buyer asking for a Western blot list needs a sub-second catalog lookup. It also breaks traceability: "the agent ran some code" is not a citable step.
- **LLM index-picking for retrieval.** Fine for a few hundred tools. Falls over on our catalog. Non-deterministic, so it cannot be unit-tested.
- **The 500-step budget and self-critic rounds.** Six iterations is the right order of magnitude for a chat turn.
- **A data lake.** Our customers are not uploading count matrices to a shopping assistant.
- **A 3000-line agent class.** Theirs bundles the loop, prompt builder, MCP server, Gradio demo, and PDF export. Ours should stay small enough to read in one sitting.

## 6. The troubleshooting question

Is Biomni a troubleshooting brain we are missing? Partly, and not in the way the question assumes.

Biomni's troubleshooting comes from the base model, two know-how documents with one troubleshooting section between them, and the ability to run analysis code on the user's data until something works. That is a data-analysis brain, not a wet-lab one.

Our system prompt already permits troubleshooting and experimental-design answers. The base model is the same kind of thing. What we lack is three narrower items:

1. **Grounded troubleshooting content.** Today those answers come from model memory with no citation, no version, no licence. Ten to twenty short guides would cover most of what a mid-size biotech asks: weak Western signal, low transfection efficiency, failed PCR, high background in immunofluorescence.
2. **The bridge from diagnosis to procurement.** In Biomni a troubleshooting session ends in a solution block. In Astor it should end in an action: a fresh lot, a different secondary antibody, a missing control, a loading buffer. Most wet-lab failures resolve to a missing or wrong item. That is the elicitation state machine and the completeness checklist doing their job. No current tool turns "my blot has no bands" into a checked list.
3. **An eval for it.** We can score equivalence. We cannot score whether a troubleshooting answer was correct, grounded, and ended in the right product. Without that we cannot tell whether adding guides helps.

None of this needs code execution or a data lake. Wet-lab troubleshooting for Astor is text reasoning over curated guides plus the catalog, which the current loop already supports once the guides exist.

## 7. Proposed next steps

Ordered by leverage. No owners assigned yet.

1. **Promote the equivalence gold set into a standing benchmark** that runs on every change to the matcher, the prompt, or the tool set. This is the single highest-return item.
2. **Define the know-how document format** for Astor: markdown, metadata block with source, licence, commercial-use flag, last-reviewed date, category. Write a loader modelled on Biomni's, about 100 lines.
3. **Write the first five troubleshooting guides** under Mary's curation process, each ending in a "what to check and what to buy" section. Pick the five techniques with the most protocol coverage in the corpus.
4. **Add the protocol-grounding sentence** to the system prompt's grounding rules.
5. **Design a troubleshooting eval**: a small set of failure descriptions with expected diagnosis, expected checklist items, and expected products.
6. **Defer** any PaperQA2-style literature agent. It remains the paused scientific-grounding half, and nothing in Biomni changes the July scope decision.

## 8. Licence note

Biomni ships a `license_info.md` alongside its main licence, and its data lake and know-how carry per-item commercial-use flags. We are borrowing patterns, not content. Before adopting any of their protocol data or know-how text, read those files. The clone stays outside the repo and is gitignored as a guard.
