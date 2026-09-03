# adv_id-only customer resolution

When the user provides only `adv_id`, resolve the customer landing page, country, industry, current metrics, and dynamic benchmark from Aeolus before running reference workflows.

Run from the project root:

```bash
python ad-creative-benchmark/scripts/aeolus_adv_context.py \
  --adv-id ADV_ID \
  --output-dir benchmark_output/ADV_ID_auto
```

The script queries Ads One Dataset `1264389`, keeps rows with non-empty `External Website URL`, and selects the row with highest `CVR (Clicks) = Conversions / Clicks`. It writes:

- `adv_context.json`: selected row plus candidate rows.
- `adv_context_candidates.csv`: all non-empty external URL candidates sorted by CVR.
- `adv_context.env`: selected `EXTERNAL_URL`, `COUNTRY`, `PRIMARY_INDUSTRY`, `SECONDARY_INDUSTRY`.

Then query current advertiser metrics from Aeolus:

```bash
python ad-creative-benchmark/scripts/aeolus_adv_metrics.py \
  --adv-id ADV_ID \
  --adv-context benchmark_output/ADV_ID_auto/adv_context.json \
  --output-dir benchmark_output/ADV_ID_auto
```

Build the dynamic benchmark from Aeolus, defaulting to the latest 30 days:

```bash
BYTEDCLI_CLOUD_SITE=i18n python ad-creative-benchmark/scripts/aeolus_dynamic_benchmark.py \
  --adv-id ADV_ID \
  --adv-context benchmark_output/ADV_ID_auto/adv_context.json \
  --output-dir benchmark_output/ADV_ID_auto \
  --strict-match-level account_l3 \
  --last-sync-days 30
```

Render the benchmark report with the selected row for downstream workflow:

```bash
python ad-creative-benchmark/scripts/benchmark_report.py \
  --adv-id ADV_ID \
  --url SELECTED_EXTERNAL_URL \
  --country SELECTED_COUNTRY \
  --industry 'SELECTED_PRIMARY-SELECTED_SECONDARY' \
  --adv-context benchmark_output/ADV_ID_auto/adv_context.json \
  --adv-data benchmark_output/ADV_ID_auto/adv_metrics_for_benchmark.csv \
  --benchmark benchmark_output/ADV_ID_auto/dynamic_benchmark_for_report.csv \
  --output-dir benchmark_output/ADV_ID_auto
```

This `--industry` is an exact selected Aeolus primary/secondary industry. If no non-empty URL rows are found, retry customer resolution with a larger lookback such as `--last-sync-days 90` before asking the user for a URL.
