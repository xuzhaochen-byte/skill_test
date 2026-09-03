# Landing-page similarity filtering

Use after `ctr_top50_videos.json/csv` is available.

Run from the project root:

```bash
python ad-creative-benchmark/scripts/filter_similar_landing_pages.py \
  --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
  --top-videos benchmark_output/ADV_COUNTRY/ctr_top50_videos.json \
  --output-dir benchmark_output/ADV_COUNTRY
```

The script:

- Loads the customer URL, country, industry, and landing-page excerpt from `benchmark_result.json`.
- Excludes candidates whose landing-page domain matches the customer domain before LLM review, because same-domain rows are likely the customer's own materials rather than external references.
- Fetches each Top CTR row's `External Website URL` when possible, following redirects.
- Calls the `sample.py`-compatible ARK/OpenAI client to judge product/landing-page similarity.
- Writes `similar_landing_pages.json` and `similar_landing_pages.csv`.

Default acceptance threshold is `similarity_score >= 0.65` and `is_similar=true`.

Similarity must be stricter than industry matching. Consider a reference suitable only when product category, purchase context, target audience/positioning, and transferability are close enough. For example, do not treat all `Clothing & Accessories` destinations as comparable to a SKIMS wedding shop; generic shoes or unrelated TikTok Shop items should be filtered out.

When landing pages return HTTP 403 or otherwise cannot be fetched, do not automatically reject them if the URL/domain/path and Aeolus context fields clearly identify the same narrow product category. Use generic evidence only: `External URL Domains`, advertiser/brand name, account-industry V40 levels, AIC category fields, product source/catalog fields, and URL path semantics. Do not hard-code particular brands or product keywords into the skill; let the LLM reason from the current customer's fields and the candidate's fields.

If no rows pass threshold, first rerun Top50 discovery with a stricter data field rather than lowering the threshold:

```bash
python ad-creative-benchmark/scripts/aeolus_ctr_top_videos.py \
  --benchmark-result benchmark_output/ADV/benchmark_result.json \
  --output-dir benchmark_output/ADV \
  --strict-match-level account_l3
```

Useful flags:

- `--workers 10`: parallelize URL fetching and LLM similarity calls. Increase carefully if the API rate limit allows it.
- `--fetch-customer`: refetch the customer landing page for a longer text sample.
- `--limit N`: analyze fewer than Top50 rows for quick tests.
- `--threshold 0.7`: tighten acceptance.
- `--no-fetch`: only use URLs/known excerpts when network fetching is blocked.
- `--allow-same-domain`: debugging only; disables the default customer-domain exclusion.
- `--allow-heuristic-similarity`: smoke-test fallback only; do not use for final business analysis.
