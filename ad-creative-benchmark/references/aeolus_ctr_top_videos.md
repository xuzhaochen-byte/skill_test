# Aeolus CTR top video discovery

Use this reference when finding CTR Top N reference creatives after the benchmark industry is known.

## Dataset and CLI

Use the visual dataset query interface, not SQL:

```bash
BYTEDCLI_CLOUD_SITE=i18n bytedcli -j aeolus viz-query -r sg --app-id 555138 --dataset-id 1264389 ...
```

Do not use `aeolus query` for this dataset; dataset `1264389` is a multi-base-table Fabric dataset and the SQL dataset query endpoint returns an unsupported-value error. `viz-query` works.

## Industry alignment

When `adv_context.json` / `benchmark_result.json.adv_context` exists, use the adv_id's Aeolus selected primary and secondary industry exactly. Do not replace it with inferred page categories or other benchmark industries.

Benchmark/manual industry strings use `Primary-Secondary`, e.g.:

```text
Clothing & Accessories-Ordinary Clothing & Shoes
```

Map this to Aeolus filters:

- `Primary Industry` / `first_industry_name_en`: `Clothing & Accessories`
- `Secondary Industry` / `second_industry_name_en`: `Ordinary Clothing & Shoes`
- `Ad Country Code` / `country_code`: input country such as `VN`

## Important field IDs

Dimensions:

- `p_date`: `10000001976403`, partition date, use `lastSync` filter.
- `Ad Country Code`: `10000002542230`, expr `country_code`.
- `Primary Industry`: `10000002542323`, expr `first_industry_name_en`.
- `Secondary Industry`: `10000002542322`, expr `second_industry_name_en`.
- `Video ID`: `10000002542392`, expr `CAST(video_id as Nullable(String))`.
- `External Website URL`: `10000002542241`, expr `external_url`.
- `Video URL`: `10000002542396`, preview URL expression.

Metrics:

- `CTR`: `10000002542839`, expr `sum(click_count)/sum([Impressions])`.
- `Impressions`: `10000002542651`, expr `show_count`.
- `Clicks (Destination)`: `10000002542652`, expr `click_count`.

## Fragile query details

- For `Impressions` and `Clicks (Destination)` as summed metrics, set aggregation to `sum(`, not `sum`.
- For numeric metric filters, use `ge` / `gt`; `gte` is not supported.
- Sort by the aggregated CTR pill ID `sum_10000002542839` descending.
- Apply a minimum denominator filter by default to avoid zero-impression or tiny-sample rows:
  - `Impressions ge 100`
- `Clicks (Destination) ge 1`
- Require non-empty `External Website URL` for reference-video discovery. The bundled script enforces this by default and over-queries before post-filtering blanks.
- If industry-only matching is still too broad, rerun with `--strict-match-level account_l3`. This uses the selected advertiser row's `Account Industry Level 3 Name V40 (Latest)` as an extra exact filter, without hard-coding any brand or product category.
- Use `--strict-match-level account_l2_l3`, `aic3`, or `aic2_aic3` only when the selected context has meaningful non-empty values and `account_l3` is still too broad. Avoid `domain` unless same-brand references are explicitly desired.
- The script includes context columns by default: `External URL Domains`, `Advertiser Name`, `Brand Name (Latest)`, account industry V40 levels, AIC category names, `Product Source`, and `Catalog Type`.

## Bundled script

Prefer the deterministic wrapper:

```bash
python ad-creative-benchmark/scripts/aeolus_ctr_top_videos.py \
  --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
  --output-dir benchmark_output/ADV_COUNTRY
```

Or pass fields directly:

```bash
python ad-creative-benchmark/scripts/aeolus_ctr_top_videos.py \
  --country VN \
  --industry 'Clothing & Accessories-Ordinary Clothing & Shoes' \
  --output-dir benchmark_output/7648503318163111956_VN
```

Outputs:

- `ctr_top50_videos.csv`
- `ctr_top50_videos.json`

The output columns include `rank`, `Video ID`, `External Website URL`, `Video URL`, domain/advertiser/brand/account-industry/AIC context fields, `CTR`, `Impressions`, and `Clicks (Destination)`.
