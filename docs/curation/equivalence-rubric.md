# Equivalence labelling rubric · substitute definition

**Status:** derived 2026-09-10 from Mary's per-pair verdicts on the 182-pair
disagreement set (`equivalence-pairs-mary.xlsx` → `equivalence-gold.csv`).
She answered every pair individually rather than the five rule questions, so
the rules below are induced from her labels; the per-rule counts are in
`equivalence-rules.csv`. Rebuild with `python -m scripts.build_equivalence_gold --db`.

## The three kinds

| kind | 中文 | meaning |
|---|---|---|
| `exact` | 相同 | Same SKU spec. Reserved for identical items that differ only cosmetically (e.g. printed vs unprinted tube). Pack-size variants are **not** exact. |
| `substitute` | 可替代 | Not the same item, but a buyer would accept it in the same protocol step without changing the experiment. |
| `none` | 不可替代 | Swapping it changes the experiment, the instrument fit, or the sample. |

Mary used `exact` once in 182 pairs. The labelling models used it 21 times, and
she overruled 13 of those to `none`. Identity is contested; the old rubric's
assumption that models settle identity and only substitution needs a human is wrong.

## Substitute rules, in precedence order

1. **Pack size only → substitute.** Same product, different quantity (25 mg vs
   5 mg, 500 vs 1000 pieces). 23/23.
2. **Capacity is a hard condition for vessels.** Different volume, well count,
   or format → none (34/40). Same capacity across brands → substitute, *unless*
   rule 3 or 4 breaks it.
3. **Sterility and grade are hard conditions.** Sterile vs non-sterile → none.
   PCR-performance-tested or low-binding vs plain → none. Barcoded vs plain
   cryovial → none. Exception: serological pipettes are assumed sterile.
4. **Species and family member are hard conditions for biologics.** Human vs
   mouse recombinant protein → none. WNT5A vs WNT6, single subunit vs
   heterodimer → none. Different inhibitor mechanism → none, even if the
   pathway is shared.
5. **Media and sera: base formulation must match exactly.** Charcoal-stripped
   or dialyzed FBS never substitutes for standard FBS, in either direction.
   Application-qualified FBS lines are mostly none against each other. Ham's
   F12 vs F12 Kaighn's → none. Same base across brands with the same additive
   (DMEM/F12 + HEPES, TMB stop buffer) → substitute.
6. **Buffers: concentration and base must match.** DPBS vs 10X PBS, Tris base
   powder vs Tris-HCl solution, LB vs TB → none.
7. **Commodity reagents and kits cross-brand → substitute.** DNA ladders,
   dNTP mixes, Proteinase K, plasmid prep kits, catalogue small molecules.
8. **Instrument-dependent options → none.** No-ROX vs High-ROX master mix,
   different well-plate notch position.

## Where the expert was uncertain or inconsistent

- The 2 ml sterile 1000-pack vs 2 ml plain 250-pack tube appears three times
  (P0225, P0849, P1419) and got substitute once and none twice. Rule 3 says
  none; the pair generator should also deduplicate.
- Tet-free FBS vs dialyzed → substitute (P0219), but cardiomyocyte-qualified
  vs dialyzed → none (P1212). Treat qualified-vs-dialyzed as none per rule 5
  until she confirms.
- Plasmid **mini**-prep vs **maxi**-prep kits → substitute (P0279, P1494).
  Scale differs; worth a second ask.
- Four pairs left as 无法判断 (freezer-box material, TC treatment unknown) are
  excluded from the gold set.

## What the labels say about the models and the matcher

On the 178 settled pairs, model two agreed with her on 95, model one on 69,
neither on 14 (kappa 0.21 and -0.07). The live confidence formula at the
production thresholds calls 157 of the 178 positive; she calls 52 positive.
Precision 0.32, and no substitute threshold between 0.70 and 1.00 lifts it
above 0.34, because the vector similarity of every pair here is already above
0.78. Rules 2 to 6 are attribute checks, not similarity: they need parsed
capacity, sterility, species, and formulation fields, which is what
`specs` is for and what the ingestion audit found mostly empty.
