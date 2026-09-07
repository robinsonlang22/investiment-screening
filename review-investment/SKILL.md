---
name: review-investment
description: Review a stock, fund, bond, index, or sector against the user's personal investment rules. Use for investment reviews, rule checks, pre-purchase checks, position reviews, or risk reviews. Retrieve Eastmoney data through mx-ds-mcp, cite evidence for each rule, and return compliant, conditional, not compliant, or insufficient information.
---

# Investment Rule Review

Treat the user's investment principles as fixed review rules. Do not invent preferences. First read [references/investment-principles.md](references/investment-principles.md). If it still contains unconfigured items, explain that the rule set is incomplete and ask for the missing principles. Do not claim to have completed a personalized review.

## Review process

1. Identify the asset name, symbol, market, intended action, target price, holding period, and position size. If the name is ambiguous, check basic information first. Ask the user only if it still cannot be identified.
2. Extract every applicable rule. Separate hard vetoes, required conditions, preferences, and risk limits. Preserve the original meaning and do not add thresholds.
3. List the facts and calculation basis needed for each rule, then use the matching Eastmoney tool from `mx-ds-mcp`:
   - A-shares: `mx_ashare_finance_data`
   - Hong Kong stocks: `mx_hk_finance_data`
   - US stocks: `mx_us_finance_data`
   - Funds: `mx_fund_finance_data`
   - Bonds: `mx_bond_finance_data`
   - Indices or sectors: `mx_index_block_finance_data`
   - Company, fund, bond, and regulatory announcements: `mx_finance_search_notice`
   - Macro, industry, and commodity data: `mx_macro_data`
4. Prefer structured data. Check announcements for material events, management commitments, risk notices, or recent changes. State the asset, metric, date range, frequency, unit, and calculation basis in each query.
5. Evaluate every rule separately. Mark missing, inconsistent, or stale evidence as `insufficient information`; never treat missing data as a pass.
6. Apply the summary rules below. Clearly separate facts, inferences, and decisions the user still needs to make.

## Decision rules

- **Not compliant**: A hard veto is triggered or a required condition fails.
- **Conditional**: No hard veto is triggered, but a preference fails, an important fact is unverified, or compliance depends on price, size, or holding period.
- **Compliant**: All hard and required conditions pass, evidence is sufficient, and risk limits are respected.
- **Insufficient information**: Evidence is inadequate to evaluate a key rule.

If the rule library defines a different aggregation method, use it. A score never overrides a hard veto.

## Output format

Lead with the decision, then the evidence:

```text
Decision: Compliant / Conditional / Not compliant / Insufficient information
Asset: Name (symbol, market)
Action: Buy / Add / Hold / Other
Data as of: YYYY-MM-DD (show dates per metric when they differ)

Key reasons:
1. ...
2. ...

Rule-by-rule review:
| Rule | Type | Evidence and basis | Result |
|---|---|---|---|
| ... | Hard veto / Required / Preference / Risk limit | Value, period, source tool | Pass / Fail / Insufficient information |

Main risks and contrary evidence:
- ...

Still needed:
- ...

Note: This checks consistency with the user's own rules. It does not guarantee returns.
```

Keep the review auditable. Give a date or period for every value, show formulas for calculated values, and include an announcement's title and date when cited. Do not present model inferences as database facts. Ask for target price, position size, or holding period only when an applicable rule needs it; otherwise complete the review with available data.
