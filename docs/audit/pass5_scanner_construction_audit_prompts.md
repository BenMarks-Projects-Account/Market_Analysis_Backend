# BenTrade Foundation Audit — Pass 5: Options Scanner Construction & Candidate Quality
## Copilot Prompts for Generating Audit Documentation

> **How to use**: Run each prompt below in Copilot with full codebase context. Save each output as the filename indicated. Upload all outputs for review.
>
> **Why this pass exists**: Passes 1-4 verified that data is ingested correctly, formulas compute correctly, pipelines filter correctly, and models reason correctly. But none of them examined whether the options scanner **builds the right candidates in the first place**. The construction logic (Phases A and B) is where "what trades does BenTrade look for" gets encoded. If the candidate pool doesn't contain good trades, perfect scoring and ranking can't help.
>
> **Focus areas** (from your prioritization):
> 1. Phase A narrowing — strike/DTE selection logic
> 2. Phase B construction — how each family builds candidates
> 3. Candidate quality — are the generated trades actually good?

---

## Prompt 5A: Phase A Narrowing — Strike & DTE Selection Logic
**Output filename**: `audit_5a_phase_a_narrowing.md`

```
I need a detailed audit of Phase A — the narrowing phase that determines which strikes and expirations enter candidate construction. This is where the scanner decides what raw material it has to work with.

Trace the full narrowing logic in `app/services/scanner_v2/data/narrow.py`, `expiry.py`, and `strikes.py`:

1. **Expiration filtering** (`expiry.py`):
   - Show the COMPLETE expiration selection logic
   - How is the DTE window applied? (dte_min, dte_max per family)
   - Are weekly expirations treated differently from monthly?
   - Is there a preference for specific expiration types (Friday, monthly, quarterly)?
   - How many expirations typically survive filtering for each family? Show real examples if possible.
   - What happens when no expirations survive? (e.g., holiday week with no weeklies in window)
   - Is there an "ideal DTE" concept, or is every expiration within the window treated equally?

2. **Strike filtering** (`strikes.py`):
   - Show the COMPLETE strike selection logic
   - How is strike distance computed? (% from spot? absolute $? standard deviations?)
   - What's the maximum/minimum distance from spot price?
   - How does moneyness filtering work? (OTM only? ATM included? ITM allowed?)
   - Are strikes filtered differently for puts vs calls?
   - Are strikes filtered differently per family? (wider for IC, tighter for butterflies?)
   - How many strikes typically survive per expiration? Show examples for SPY at $545.

3. **The narrow_chain() function** (`narrow.py`):
   - Show the complete orchestration — how expiry filtering and strike filtering combine
   - What's the output shape? (V2NarrowedUniverse — show the data structure)
   - How are strikes grouped? (by expiration? by option_type?)
   - Is there deduplication at this stage? (same strike in multiple chains)
   - What metadata is preserved vs discarded during narrowing?

4. **Per-family narrowing configuration**:
   For each of the 4 families (vertical_spreads, iron_condors, butterflies, calendars):
   - What DTE window is used?
   - What strike distance range is used?
   - What moneyness filters are applied?
   - Are there family-specific narrowing overrides beyond DTE/strike?
   - Show the configuration source (is it in the family module, base scanner, or registry?)

5. **Quality of narrowing decisions**:
   - Does the narrowing logic prefer liquid strikes (high OI) or just use distance?
   - Is there any IV-aware strike selection? (e.g., prefer strikes where IV is higher for selling)
   - Is there any delta-targeted selection? (e.g., "find the 16-delta put" for premium selling)
   - Does the narrowing consider bid-ask spread quality?
   - Is ATR used to determine appropriate strike distance? (2 ATR OTM vs fixed %)

6. **What gets lost in narrowing**:
   - Can you identify scenarios where a potentially good trade is excluded by Phase A?
   - Example: A 7-DTE put credit spread where dte_min=7 — does the "=" include or exclude exactly 7?
   - Example: SPY at $545, short put at $530 (2.75% OTM) — does this survive the distance filter?
   - Example: A $1-wide strike that falls outside the distance range but would make a good butterfly center

Produce a narrowing flow diagram and findings on whether Phase A produces the right raw material for each family's construction phase.
```

---

## Prompt 5B: Phase B Construction — Vertical Spreads & Iron Condors
**Output filename**: `audit_5b_verticals_and_ic.md`

```
I need a detailed audit of how vertical spreads and iron condors are constructed in Phase B. These are the highest-volume families and the core income strategies.

Trace the construction logic in `app/services/scanner_v2/families/vertical_spreads.py` and `iron_condors.py`:

PART 1: VERTICAL SPREAD CONSTRUCTION

1. **Leg pairing logic**: 
   - How does the builder enumerate all possible (short, long) leg pairs?
   - Is it a simple O(n²) cross-product of all strikes, or is there intelligent filtering?
   - How is the short leg selected? (closest to target delta? all OTM strikes?)
   - How is the long leg selected? (fixed width from short? range of widths?)
   - Are put credit spreads and call credit spreads built by the same code path or different?

2. **Width selection**:
   - What widths are generated? ($1, $2, $5, $10, $20, $50?)
   - Is there a minimum width? Maximum width?
   - Is width configurable per symbol? (SPY $5-wide vs AAPL $2.50-wide?)
   - Does the builder prefer certain widths? Or does it generate ALL possible widths?
   - How many width variations does a typical short strike produce?

3. **Credit/debit determination**:
   - How does the builder determine if a spread is credit or debit?
   - Is it based on strike relationship to spot? Or on the actual bid-ask?
   - Can a "put credit spread" scanner produce a debit spread? (and vice versa)

4. **What makes a GOOD vertical spread for income trading?**
   Evaluate the construction against these quality criteria:
   - Short strike delta in 0.15-0.30 range (standard for income)
   - Width that balances premium vs max loss ($5 is typical for SPY)
   - Net credit ≥ 20% of width (adequate premium collection)
   - Short strike below key support levels
   - DTE in 30-45 day sweet spot for theta decay

5. **Does the builder target these qualities, or just enumerate all combinations?**
   - Is there any delta-targeting in the short strike selection?
   - Is there any premium-to-width ratio filtering?
   - Is there any technical level awareness (support/resistance)?
   - Or is all quality filtering deferred to Phase D/E/credibility gate?

PART 2: IRON CONDOR CONSTRUCTION

6. **How are the 4 legs assembled?**
   - Does the builder construct put side and call side independently, then cross-product?
   - Or does it build full 4-leg condors from the start?
   - Show the exact construction algorithm (pseudocode or actual code)

7. **Symmetry and balance**:
   - Are put and call sides required to have equal width?
   - Is there a balance constraint on delta (put delta ≈ call delta)?
   - Can the builder produce a "skewed" condor (wider on one side)?
   - Is there any wing placement strategy? (e.g., put side wider than call side in bullish regime)

8. **What makes a GOOD iron condor for income trading?**
   Evaluate against these criteria:
   - Short strikes at 1-2 standard deviations OTM (16-delta area)
   - Equal or near-equal delta on both short strikes
   - Combined credit ≥ 33% of single-side width
   - Wings wide enough to capture meaningful premium
   - DTE 30-45 days for optimal theta/gamma ratio

9. **Does the builder target these qualities?**
   - Is there any delta-matching between put and call short strikes?
   - Is there any combined-credit minimum during construction?
   - Is the construction aware of the underlying's expected move?

PART 3: EXPLOSION CONTROL

10. **Generation cap**:
    - What is _DEFAULT_GENERATION_CAP (50,000)?
    - How often is the cap hit in practice? (SPY with many strikes + expirations)
    - When the cap is hit, which candidates are kept and which are discarded?
    - Is the cap applied per (scanner_key, symbol) or globally?
    - Is there a smarter approach? (e.g., cap per expiration, or filter early)

11. **Compute cost**:
    - How long does Phase B take for verticals on SPY (estimated)?
    - How long for IC on SPY?
    - Is the O(n²) or O(n⁴) construction the pipeline's bottleneck?
    - Are there any memoization or caching strategies?

Produce a construction flow diagram for both families and findings on whether the construction logic produces trades that match BenTrade's income trading philosophy.
```

---

## Prompt 5C: Phase B Construction — Butterflies & Calendars
**Output filename**: `audit_5c_butterflies_and_calendars.md`

```
I need a detailed audit of how butterflies and calendar/diagonal spreads are constructed in Phase B. These are the more complex families with unique construction challenges.

Trace the construction logic in `app/services/scanner_v2/families/butterflies.py` and `calendars.py`:

PART 1: BUTTERFLY CONSTRUCTION

1. **Triplet enumeration**:
   - How does the builder find valid (lower, center, upper) strike triplets?
   - Is symmetry required? (center - lower == upper - center?)
   - Are asymmetric butterflies supported? (broken-wing butterflies?)
   - What wing widths are generated? ($1, $2, $5, $10?)
   - How is the center strike selected? (ATM? near spot? at specific delta?)

2. **Debit butterfly construction**:
   - Buy 1 lower + Sell 2 center + Buy 1 upper (calls) or equivalent puts
   - Show the exact leg assembly logic
   - How does the builder handle the 2x center quantity?
   - Is the center strike required to be ATM, or can it be anywhere?

3. **Iron butterfly construction**:
   - Sell 1 ATM put + Sell 1 ATM call + Buy 1 OTM put + Buy 1 OTM call
   - How does the builder define "ATM"? (nearest strike to spot? within $X?)
   - Are the wings required to be symmetric?
   - Show the exact construction logic

4. **What makes a GOOD butterfly for BenTrade's use case?**
   - Center strike at expected price target (directional bet) or ATM (neutral bet)
   - Wing width that matches expected move range (1-2 standard deviations)
   - Net debit ≤ 30% of wing width (acceptable risk)
   - DTE 14-30 days (butterfly payoff peaks near expiration)
   - Adequate liquidity at all 3 strike points

5. **Does the builder target these qualities?**
   - Is there any expected-move awareness in center strike placement?
   - Is there any debit-to-width ratio filtering during construction?
   - Does the builder check liquidity at all 3 strikes during construction?
   - Or is everything deferred to later phases?

6. **Pass 2 finding**: Butterfly EV uses binary-outcome model. Does the construction phase have any awareness of the triangular payoff issue? Or does it build butterflies identically to other spreads?

PART 2: CALENDAR/DIAGONAL CONSTRUCTION

7. **Cross-expiration pairing**:
   - How does the builder select near and far expirations?
   - What's the minimum/maximum DTE spread between legs?
   - Is there a preferred DTE ratio? (e.g., near at 30 DTE, far at 60 DTE?)
   - How many expiration pairs are generated per symbol?

8. **Strike selection for calendars**:
   - Calendar = same strike, different expirations. How is the strike chosen?
   - ATM only? Near-the-money? Range of strikes?
   - Is there any IV term structure awareness? (calendars profit from IV differential)

9. **Strike selection for diagonals**:
   - Diagonal = different strikes, different expirations. How are the strikes related?
   - Is the far leg more OTM than the near leg? (typical diagonal)
   - What strike offset range is used?

10. **What makes a GOOD calendar spread for income trading?**
    - Same strike at ATM or slightly OTM
    - Near leg 25-35 DTE, far leg 55-70 DTE (ratio ~2:1)
    - IV of far leg > IV of near leg (positive vega position benefits from vol increase)
    - Net debit reasonable relative to potential spread widening
    - Underlying not expected to make large directional moves

11. **Does the builder target these qualities?**
    - Is there IV term structure analysis during construction?
    - Is there any expected-move filtering?
    - Is there DTE ratio optimization?
    - Does construction consider whether the calendar is ATM (neutral) vs OTM (directional)?

PART 3: BOTH FAMILIES

12. **Cross-family quality**: Compare butterfly and calendar construction sophistication:
    - Which family has smarter construction? (delta-aware? IV-aware? liquidity-aware?)
    - Which family generates more noise (candidates that will fail later phases)?
    - Which family would benefit most from construction-phase intelligence?

Produce construction flow diagrams for both families and findings on construction quality and the gap between "what gets built" and "what a trader would actually want."
```

---

## Prompt 5D: Candidate Quality Assessment — Are These Good Trades?
**Output filename**: `audit_5d_candidate_quality.md`

```
I need an assessment of whether the options scanner produces candidates that represent trades a knowledgeable options trader would actually want to take. This is the most important audit in Pass 5.

Use the full pipeline trace from 3B (options pipeline flow) and the construction logic from 5A-5C as context.

PART 1: VERTICAL SPREAD QUALITY

1. **Take a concrete example**: SPY at $545, 30 DTE. Walk through what the put_credit_spread scanner actually produces:
   - How many expirations are in the 1-90 DTE window?
   - How many OTM put strikes survive Phase A narrowing?
   - How many (short, long) pairs are constructed in Phase B?
   - How many survive Phases C, D, D2?
   - How many pass the credibility gate?
   - What do the top-5 by EV look like? (strikes, credit, width, POP, EV)
   - Would an experienced options trader take any of these?

2. **Delta distribution of short strikes in top candidates**:
   - Are the short strikes clustered around the "sweet spot" (16-30 delta)?
   - Or are they scattered across all OTM strikes?
   - Do any ultra-far-OTM shorts (delta < 0.05) survive to the top-30?
   - Do any near-ATM shorts (delta > 0.40) survive?

3. **Width distribution in top candidates**:
   - What spread widths appear in the final output? ($1? $5? $10? $20?)
   - Is there a bias toward wider spreads? (Pass 2 found raw EV favors wider)
   - What's the typical credit-to-width ratio in the top-30?
   - Are there any $1-wide spreads in the output? (These are often illiquid and marginal)

4. **DTE distribution in top candidates**:
   - What DTE values appear in the final output?
   - Is there clustering at specific expirations (weekly, monthly)?
   - Are 7-DTE spreads competing with 45-DTE spreads? (very different risk profiles)
   - Is there any DTE optimization or is it just "everything in the window"?

PART 2: IRON CONDOR QUALITY

5. **Walk through an IC example**: SPY at $545, 30 DTE:
   - What do the top-3 iron condors look like? (all 4 strikes, credits, widths)
   - Is the delta balanced between put and call sides?
   - Is the combined credit adequate (≥33% of single-side width)?
   - Are the wings placed sensibly relative to expected move?

6. **Skew handling**:
   - SPY put options are typically more expensive than equidistant call options (put skew)
   - Does the IC builder account for this? (wider put side to collect more premium?)
   - Or are both sides built independently with no skew awareness?

PART 3: CALENDAR/DIAGONAL QUALITY

7. **Calendar trade viability**:
   - Since EV=None, calendars can't be ranked by the current system
   - But are the CONSTRUCTED calendars actually viable trades?
   - What DTE pairs are typical? (is the near/far ratio sensible?)
   - Are the strike placements reasonable (ATM for neutral, slight OTM for directional)?
   - Would a trader consider these setups if they could see them?

PART 4: CROSS-FAMILY COMPARISON

8. **Which family produces the highest-quality candidates overall?**
   - Rank: verticals, IC, butterflies, calendars by construction quality
   - Which family generates the most "noise" (candidates that fail later)?
   - Which family has the biggest gap between "what's constructed" and "what's good"?

9. **What's missing from ALL families?**
   Common quality factors not considered during construction:
   - Technical level awareness (support/resistance at short strike)
   - IV rank/percentile context (is IV high enough to sell?)
   - Expected move calibration (are wings outside the expected move?)
   - Earnings/event proximity filtering
   - Greeks-based optimization (target specific theta/delta profiles)

10. **If you could add ONE construction-phase improvement to each family, what would it be?**
    For each family, identify the single highest-impact change that would most improve the quality of constructed candidates before they enter Phase C.

Produce a quality scorecard per family and findings on the gap between "what gets built" and "what a trader would actually want."
```

---

## Prompt 5E: Registry, Configuration & Family Coordination
**Output filename**: `audit_5e_registry_and_config.md`

```
I need an audit of how the V2 scanner families are registered, configured, and coordinated. This covers the infrastructure that connects Phase A narrowing to Phase B construction across all 11 scanner keys.

Trace the registry and configuration in `app/services/scanner_v2/registry.py`, `base_scanner.py`, and family-level configs:

1. **Registry architecture** (`registry.py`):
   - How are scanner keys mapped to family implementations?
   - Show the complete mapping: scanner_key → family class → Phase B builder
   - How does the registry resolve which narrowing config to use per scanner_key?
   - Is the registry a singleton? How is it initialized?

2. **Scanner key → family → behavior mapping**:
   For each of the 11 scanner keys, document:
   - What family class handles it?
   - What Phase B builder method is called?
   - What narrowing configuration (DTE range, strike distance, moneyness) is used?
   - What Phase E math is applied? (vertical default? IC override? butterfly? calendar?)
   - Are there any scanner_key-specific overrides beyond the family default?

3. **BaseV2Scanner hooks** (`base_scanner.py`):
   - What hook methods can families override?
   - Which families override which hooks?
   - Is there a consistent pattern, or does each family do something different?
   - Show the hook resolution order (base → family → scanner_key-specific)

4. **Configuration sources**:
   - Where do DTE windows come from? (hardcoded in family? in registry? in base scanner?)
   - Where do strike distance ranges come from?
   - Where do width limits come from?
   - Is there a single place to see ALL configuration for a given scanner_key?
   - Or is configuration scattered across registry + family + base scanner?

5. **Cross-family coordination**:
   - When 11 scanner keys run on the same symbol, do they share any state?
   - Do later scanners benefit from earlier scanners' chain data (caching)?
   - Is there any deduplication between families? (e.g., a vertical that's also a condor side)
   - Are there any inter-family constraints? (e.g., "don't build IC if vertical already exists")

6. **Scanner key variants within a family**:
   - Vertical spreads have 4 variants (put_credit, call_credit, put_debit, call_debit)
   - How do these differ in construction? (just option_type and side, or deeper differences?)
   - Are all 4 using the same narrowing config?
   - Are credit and debit variants using the same Phase B builder?

7. **Missing from registry/configuration**:
   - Is there any mechanism for enabling/disabling specific scanner keys?
   - Is there any mechanism for adjusting a scanner_key's aggressiveness? (strict vs wide)
   - Is there a way to add a new scanner_key without modifying existing code?
   - Does the registry support versioning? (v2 scanner vs potential v3)

8. **Consistency check**:
   - Do all families follow the same lifecycle? (narrow → construct → validate → hygiene → math → normalize)
   - Are there any families that skip phases or add extra phases?
   - Is the Phase A → Phase B interface consistent across families?
   - Are V2Candidate fields consistently populated by all families?

Produce a registry map showing the complete scanner_key → family → config → behavior chain for all 11 keys, and findings on configuration clarity and cross-family coordination.
```

---

## Running Order

Run the prompts in order (5A through 5E):
- **5A** audits Phase A narrowing (what raw material enters construction)
- **5B** audits vertical spread and iron condor construction (highest-volume families)
- **5C** audits butterfly and calendar construction (complex families)
- **5D** assesses whether the constructed candidates are actually good trades
- **5E** audits the registry and configuration infrastructure

Once you have all 5 files, upload them and I'll produce the final findings and fix specifications for scanner construction.

---

## What I'll Be Looking For

1. **Brute-force vs intelligent construction** — does Phase B just enumerate all combinations, or does it target high-quality setups?
2. **Delta awareness** — are short strikes selected by delta target, or just by distance from spot?
3. **Width optimization** — are spread widths chosen for the right reasons, or just "all possible widths"?
4. **IV awareness** — does any family consider implied volatility during construction?
5. **Expected move calibration** — are wings placed relative to the expected move?
6. **The noise problem** — how many candidates are built just to be rejected later? Could earlier filtering save compute?
7. **Calendar viability** — are calendar constructions good enough that they'd be worth ranking if the EV=None issue were solved?
8. **Missing quality signals** — what does a human trader consider that the scanner doesn't?
