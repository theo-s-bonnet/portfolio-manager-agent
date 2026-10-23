# Higgons screener: Saturday 2026-10-24
Strategy 2.0 · config 2.0.0 · commit a1b2c3d · run on 2026-10-24 at 08:14 · duration 6 min 12 s
Sources: TradingView (delayed data). Exchange rates: last Yahoo Finance close available on 2026-10-24.

## Summary

| Step | Remaining | Excluded at this step | of which missing data |
|---|---|---|---|
| TradingView universe (2 markets) | 3 | — | — |
| R3.1 PEA universe: market, headquarters, primary listing | 3 | 0 | 0 |
| R3.2 market cap | 3 | 0 | 0 |
| R3.3 liquidity (approximate) | 3 | 0 | 0 |
| R3.4 stocks only (preferred shares excluded outside Germany) | 3 | 0 | 0 |
| R3.5 banks and insurers excluded | 3 | 0 | 0 |
| R4.1 retained P/E | 3 | 0 | 0 |
| R4.3 5-year revenue growth | 3 | 0 | 0 |
| R4.4 net debt / EBITDA | 3 | 0 | 0 |
| R4.5 price above the EMA | 3 | 0 | 0 |
| R4.6 6-month performance | 3 | 0 | 0 |
| R3.6 price history (first TradingView bar) | 3 | 0 | 0 |
| R4.11 data needed by the score | 2 | 1 | 1 |
| R4.12 EBITDA and EPS denominators | 2 | 0 | 0 |
| Yahoo ticker available | 2 | 0 | 0 |
| **Eligible** | **2** |  |  |

Each stock is counted at the first step it fails.

## Ranking

| # | Stock | Name | Country | Sector | Score | Perf 6M | P/E | ROE | Rev. CAGR 5y | ND/EBITDA |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | OSL:BBB (BBB.OL) | Bravo ASA | Norway | Transportation | 75.0 | +40.0% (100) | 9.0 (0) | 25.0% (100) | 12.0% (100) | −0.5 (100) |
| 2 | EURONEXT:AAA (AAA.PA) | Alpha SA | France | Producer Manufacturing | 25.0 | +25.0% (0) | 6.0 (100) | 18.0% (0) | 8.0% (0) | 0.5 (0) |

Raw value, then percentile rank among eligible stocks in parentheses.

## Excluded for missing or doubtful data

Stocks that pass every evaluated rule except one or more rules in `missing`.

Stocks concerned: 1.

| Stock | Rules in `missing` | Missing or doubtful data |
|---|---|---|
| XETR:CCC (CCC.DE) | R4.11 | ROE missing; fallback not computed: shareholders' equity -100,000,000 ≤ 0 |

## Warnings

### TradingView markets with errors or truncated (0)
None.

### Retained P/E: fallback to market cap / TTM net income (TradingView P/E missing) (0)
None.

### Statutory country (ISIN prefix) outside the EU/EEA: check PEA eligibility (0)
None.

### Retained ROE: fallback to TTM net income / shareholders' equity (TradingView ROE missing) (0)
None.

### ROE ≤ 0, ranked last on quality (0)
None.

### Eligible by country (2)
- France: 1
- Norway: 1

## Rules not applied or applied with a limitation

| Rule | Planned version | Reason |
|---|---|---|
| R3.1 | implemented in V1, with a limitation | headquarters country from TradingView (may be the operational seat); PEA eligibility confirmed with the broker |
| R3.3 | implemented in V1, with a limitation | 90-day average volume x last price |
| R3.4 | implemented in V1, with a limitation | SPACs listed as common stock cannot be detected |
| R1 | out_of_scope | overall allocation, checked manually |
| R2.4 | V3 | — |
| R6 | V5 | portfolio construction |
| R8 | V5 | allocation between brokerage accounts |
| S1-S5 | V3 | positions and exit rules (S2 in V6) |
| R10 | V3 | trade journal |
