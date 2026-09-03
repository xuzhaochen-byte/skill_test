# Dynamic benchmark workflow reference

Use this reference for the benchmark-only step. The benchmark source is Aeolus.

Before running benchmark/Aeolus commands, verify `bytedcli` is installed and authenticated for Aeolus access:

```bash
python ad-creative-benchmark/scripts/preflight_check.py --workflow benchmark
```

Stop and ask the user to run their bytedcli authentication/setup flow if this check fails. The checker confirms local readiness; if Aeolus access has not been validated in this shell/session, ask the user to complete their normal `bytedcli` login/access validation before starting the long benchmark workflow.

## Inputs produced by Aeolus scripts

- `adv_context.json`: selected advertiser row from `aeolus_adv_context.py`.
- `adv_metrics_for_benchmark.csv`: current advertiser metrics from `aeolus_adv_metrics.py`.
- `dynamic_benchmark_for_report.csv`: dynamic P10-P90 cohort percentiles from `aeolus_dynamic_benchmark.py`.

## Dynamic benchmark rules

1. Resolve the customer by `adv_id` in Aeolus and select the highest-CVR row with non-empty `External Website URL`.
2. Use the selected Aeolus `Ad Country Code`, `Primary Industry`, and `Secondary Industry` as the benchmark industry; do not classify the landing page with an LLM.
3. Build the benchmark cohort in Aeolus with the same country and same primary/secondary industry, non-empty external URL, `Impressions >= 100`, `Clicks >= 1`, and default `p_date = lastSync 30 day(s)`.
4. Tighten by `Account Industry Level 3 Name V40 (Latest)` when it exists (`--strict-match-level account_l3`).
5. Exclude rows with the same `adv_id`, same normalized landing-page domain, or same advertiser name.
6. Compute P10-P90 locally from returned cohort rows. `viz-query` percentile/quantile aggregation is not reliable enough to be the source of truth.
7. Stop if no exact dynamic row exists for `country_code + industry_category`; rerun `aeolus_dynamic_benchmark.py` for the selected adv context instead of falling back to static or global rows.

## Metric interpretation

Higher is better:

- `ctr`
- `cvr`
- `play_3s_ratio`
- `total_cost` / benchmark `cost_*`

For `total_cost`, interpret the value as total spend/consumption scale, not unit cost. A higher raw percentile is a higher benchmark waterline and should not be inverted.

## Output files

`benchmark_report.py` writes:

- `benchmark_result.json`: structured data and diagnostics.
- `report-data.js`: JS assignment consumed by the frontend template.
- `index.html`, `styles.css`, `app.js`: static visual report.
