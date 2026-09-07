# My Investment Rules

> Status: In progress. Use configured rules for reviews. The agent must not invent unset portfolio limits.

## How to use this file

Write each principle as a testable rule with a clear scope. Treat any unspecified threshold or limit as unset.

## Technical rules

### P1: Moving Average Alignment

- Scope: Listed stocks reviewed with daily price data.
- Level and type: Primary required rule. Items marked preferred are preferences.
- Data: Forward-adjusted daily closing prices. `MA5`, `MA10`, and `MA20` are the core averages; `MA60` provides background only. Use at least 120 trading days.
- Recency: Use the latest closed trading day on the review date. Do not use an intraday price for the final decision.

#### State definitions

1. **Core upward**: The 10-day slopes of `MA5`, `MA10`, and `MA20` are all above zero, and `MA10 > MA20`.
2. **Core bullish alignment**: `MA5 > MA10 > MA20`, and all three core averages are rising.
3. **Core bearish**: All three core averages are falling, or `MA10 ≤ MA20`.
4. **Mean core moving average**: For `m=3`:

   ```text
   μ(t) = [MA5(t) + MA10(t) + MA20(t)] / 3
   ```

5. **Range and relative range**:

   ```text
   R(t) = max(MA5, MA10, MA20) - min(MA5, MA10, MA20)
   r(t) = R(t) / μ(t) × 100%
   ```

6. **Daily density**: The current threshold is `δ=2%`. A day is dense (`Q_t=1`) when `r(t)≤2%`; otherwise `Q_t=0`. Report `μ(t)`, `R(t)`, and `r(t)`. Use a new threshold if the user changes `δ`.
7. **Moving-average slope**: Use the average daily change over the latest 10 trading days:

   ```text
   S10(MA) = [MA(t) / MA(t-10) - 1] / 10 × 100%
   ```

   This measures moving-average direction only. It does not replace P2's 30-day main regression or 10-day short-term regression.
8. **MA60 background**:
   - Calculate and report `MA60` and `S10(MA60)`, but do not use them to decide P1.
   - If `MA20 > MA60` and `S10(MA60)>0`, report that medium- and long-term trends are aligned upward.
   - If `MA20 ≤ MA60` or `S10(MA60)≤0`, report that they are not yet aligned.
   - An MA60 state alone cannot make P1 fail or become conditional.

#### Review logic

- Required: Core upward state.
- Preferred: Core bullish alignment.
- If core upward is not met: **Fail**.
- If core upward is met but bullish alignment is not: **Conditional pass**.
- If bullish alignment is met: **Pass**.
- Report daily density separately for P2 clock-3 reviews. It does not change the P1 direction result.
- Exceptions: None set.
- Evidence priority: Forward-adjusted daily closing prices and averages calculated from them. If a tool supplies averages directly, verify their periods.

### P2: Price Trend

- Scope: Listed stocks reviewed for a buying opportunity with daily price data.
- Type: Required buying rule. Clock 4 is a hard veto. Clock 2 is the strongest preference.
- Data: Use a log-price linear regression over the latest 30 trading days for the main direction. Use a 10-day regression only for short-term acceleration. Never classify direction from a single-day move or chart appearance.
- Recency: Use the latest closed trading day on the review date.

#### Calculations

Let the latest 30 trading days be `i = 0, 1, ..., 29`, with closing price `Close_i`. Apply the same method to the latest 10 days for the short-term measures.

1. **Regression**:

   ```text
   ln(Close_i) = a + b·i + ε_i
   ```

2. **Normalized daily trend**:

   ```text
   G = [exp(b) - 1] × 100%
   ```

   The 30-day value is `G30`. It is the only main clock-classification metric.
3. **Endpoint cross-check**:

   ```text
   E = [Close_last / Close_first - 1] / (n - 1) × 100%
   ```

   Use `E30` to verify direction. If `G30` and `E30` have opposite signs, mark the main direction unstable and do not return an unconditional pass.
4. **Trend stability**: Report `R²30`. Treat `R²30 ≥ 0.60` as stable. Treat `R²30 < 0.60` as volatile or unstable and review it as a clock-3 candidate. The user may change this threshold.
5. **Short-term acceleration**: Calculate `G10`, `E10`, and `R²10`. These describe short-term movement but do not change the main clock class:
   - Opposite signs for `G10` and `G30`: short-term divergence.
   - Same sign and `|G10| > |G30|`: acceleration.
   - Same sign and `|G10| < |G30|`: slowdown.
   - Equal values: similar speed.
   - Opposite signs for `G10` and `E10`, or `R²10 < 0.60`: also mark short-term direction unstable.

#### Clock classification

- **Clock 1 — Fast rise**: `G30 > 0.50%/day` and `R²30 ≥ 0.60`.
- **Clock 2 — Steady rise**: `0.15%/day < G30 ≤ 0.50%/day` and `R²30 ≥ 0.60`.
- **Clock 3 — Flat or volatile**: `-0.15%/day ≤ G30 ≤ 0.15%/day`, or `R²30 < 0.60`.
- **Clock 4 — Slow decline**: `-0.50%/day ≤ G30 < -0.15%/day` and `R²30 ≥ 0.60`.
- **Clock 5 — Fast decline**: `G30 < -0.50%/day` and `R²30 ≥ 0.60`.

Apply the boundaries exactly as written.

#### Clock-1 buying window

1. **Entry day**: The first trading day of the current continuous clock-1 period. It qualifies for clock 1 and the previous day does not.
2. **Continuous period**: Every day from entry through the current day meets `G30 > 0.50%/day` and `R²30 ≥ 0.60`. Restart after any interruption.
3. **Trading-day condition**: Entry is day 1. This condition passes on days 1 through 10.
4. **Return since entry**:

   ```text
   R_entry = [Close_t / Close_entry - 1] × 100%
   ```

5. **Return condition**: Pass when `R_entry ≤ 15%`; exactly 15% passes.
6. **Either condition passes**: Pass the window if the trading-day condition or return condition passes. Fail only when more than 10 trading days have passed and `R_entry > 15%`.
7. If the entry day or its closing price is missing and neither condition can be evaluated, return **Insufficient information**.

#### Review logic

1. **Clock 1**: Pass if within trading days 1–10 after entry, or if `R_entry ≤ 15%`. Fail only if both conditions fail.
2. **Clock 2**: Highest preference; pass when its definition is met.
3. **Clock 3**: Pass only when P1's moving averages are dense; otherwise fail.
4. **Clock 4**: Avoid completely. Trigger the hard veto and fail P2.
5. **Clock 5**: A counter-trend candidate is allowed only after the latest close rises above `MA10`:
   - `Close ≤ MA10`: Fail.
   - `Close > MA10` and `Close ≤ MA20`: Conditional pass.
   - `Close > MA10` and `Close > MA20`: Pass; this is preferred.
6. Return **Insufficient information** if `G30` and `E30` disagree in direction, or if the 30-day regression, `R²30`, `MA10`, `MA20`, or required density result is missing. If only the 10-day regression is missing, classify from complete 30-day data and note that short-term acceleration data is missing.

- Exceptions: None set.
- Evidence priority: Eastmoney daily closing prices and the derived 30-day regression, 10-day regression, `MA10`, `MA20`, and P1 density.

## Fundamental rules

Not configured. Start numbering at P3. Future rules should cover earnings quality, growth, solvency, cash flow, valuation, industry, and governance.

## Portfolio and execution limits

- Maximum position in one asset: Unset
- Maximum position in one industry: Unset
- Allowed asset types and markets: Unset
- Entry-price or valuation discipline: Unset
- Minimum or maximum holding period: Unset
- Stop-loss, trimming, or exit rules: Unset
- Other hard vetoes: Unset

## Overall decision

- Any hard veto triggered: **Not compliant**
- Any required condition failed: **Not compliant**
- Only a preference failed, or a key fact remains unverified: **Conditional**
- All hard and required conditions passed with sufficient evidence: **Compliant**
