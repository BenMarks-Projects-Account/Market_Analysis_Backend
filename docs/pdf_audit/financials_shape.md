# Financials shape — verdict on partition correctness

## Source path

```
ce_result.raw_financials.company_data.financials_annual.statements : list[dict]
```

Each element is one fiscal period (annual). The list is ordered most-recent-first.

The PDF reads from this exact path at
`on_demand_pdf_service.py` L271-L274 — confirmed correct.

## Sample statement (from frontend mock fixture, L2110-L2125)

```json
{
  "period": "2024-12-31",
  "fiscal_year": 2024,
  "fiscal_period": "FY",
  "revenue": 38000000000,
  "cost_of_revenue": 17100000000,
  "gross_profit": 20900000000,
  "operating_expenses": 10400000000,
  "research_and_development": 5200000000,
  "selling_general_administrative": 5200000000,
  "operating_income": 10500000000,
  "income_before_tax": 9950000000,
  "income_tax": 1950000000,
  "net_income": 8000000000,
  "eps_basic": 7.18,
  "eps_diluted": 7.05,
  "basic_avg_shares": 1114000000,
  "diluted_avg_shares": 1134000000,
  "total_assets": 55000000000,
  "current_assets": 22000000000,
  "noncurrent_assets": 33000000000,
  "fixed_assets": 4000000000,
  "inventory": null,
  "accounts_payable": 3500000000,
  "total_liabilities": 30000000000,
  "current_liabilities": 11000000000,
  "noncurrent_liabilities": 19000000000,
  "long_term_debt": 14000000000,
  "total_equity": 25000000000,
  "equity_parent": 25000000000,
  "operating_cash_flow": 11000000000,
  "investing_cash_flow": -1500000000,
  "financing_cash_flow": -8500000000,
  "net_cash_flow": 1000000000,
  "free_cash_flow": 9500000000
}
```

## Period identifiers — all four are usually present

| Key             | Type | Example       | PDF priority (post Phase 2.1)         |
|-----------------|------|---------------|---------------------------------------|
| `fiscal_year`   | int  | `2024`        | **Tier 1** — preferred when present   |
| `fiscal_period` | str  | `"FY"`        | Concatenated with year → `"FY2024"`   |
| `period`        | str  | `"2024-12-31"`| Tier 2 — year extracted via regex      |
| `end_date`      | str  | `"2025-12-31"`| Tier 3 fallback                       |
| `start_date`    | str  | `"2025-01-01"`| Tier 3 fallback                       |

Verdict: ✅ The Phase 2.1 `_partition_statements` handles all four
correctly. No bug.

## Field-name verdict (PDF constants vs actual statement keys)

The PDF declares three tuples at `on_demand_pdf_service.py` L84-L122:

### `INCOME_STATEMENT_FIELDS` — ✅ all 14 names match

```
revenue, cost_of_revenue, gross_profit, operating_expenses,
research_and_development, selling_general_administrative,
operating_income, income_before_tax, income_tax, net_income,
eps_basic, eps_diluted, basic_avg_shares, diluted_avg_shares
```

All present in the sample statement above with matching snake_case names.

### `BALANCE_SHEET_FIELDS` — ✅ all 12 names match

```
total_assets, current_assets, noncurrent_assets, fixed_assets,
inventory, accounts_payable, total_liabilities, current_liabilities,
noncurrent_liabilities, long_term_debt, total_equity, equity_parent
```

All present.

### `CASH_FLOW_FIELDS` — ✅ all 5 names match

```
operating_cash_flow, investing_cash_flow, financing_cash_flow,
net_cash_flow, free_cash_flow
```

All present.

## Mismatches

**None found** in the mock fixture vs PDF constants. ⚠ Real API may
include additional fields the PDF does not surface (e.g. fields like
`ebitda`, `operating_lease_obligations`, etc.). Those are silently
ignored by the partition function (which only emits the fields named in
the constants), which is the intended behavior.

## Validation step

After the backend is up, run:

```powershell
python scripts/dump_ce_result.py --job-id <real-job-id> --out docs/pdf_audit/ce_result_sample.json
```

Then `jq` the first statement to confirm the keys match:

```powershell
python -c "import json; s=json.load(open('docs/pdf_audit/ce_result_sample.json'))['raw_financials']['company_data']['financials_annual']['statements'][0]; print(sorted(s.keys()))"
```

Compare the printed list against `INCOME_STATEMENT_FIELDS +
BALANCE_SHEET_FIELDS + CASH_FLOW_FIELDS + ('period','fiscal_year','fiscal_period','end_date','start_date')`.
Any extra real-API keys are candidates for extending the constants.
