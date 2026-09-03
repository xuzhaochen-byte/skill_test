---
name: ad-creative-benchmark
description: Analyze TikTok ad delivery benchmarks, generate ad creative assets, and prepare disabled TikTok campaign upload/create plans from advertiser IDs, URLs, images, or videos. Use when the user asks to analyze advertising benchmark/performance/reference creatives from adv_id or landing-page URL; find CTR Top reference videos; filter/download/analyze reference videos into Reusable Creative Patterns; create TikTok image/video materials from any product URL, source image, source video, or URL-derived assets while optionally adapting matching benchmark shooting/editing patterns; or upload generated/selected image/video materials and create a disabled campaign/adgroup/ad draft.
---

# Ad Creative Benchmark

Use this skill when the user asks for either benchmark analysis or creative-asset generation from ad/business inputs. It supports these connected workflows:

1. **Benchmark/reference analysis**: benchmark the advertiser, discover high-CTR references in the matched country/industry, filter references by landing-page/product similarity, download reference videos, and analyze them into reusable creative-production patterns.
2. **URL-to-creative generation**: crawl a landing-page URL, extract URL/product/image information, plan image creatives and video variants, generate image assets, and submit Ark image-to-video jobs. When benchmark reference-video analysis exists, video planning and caption generation may selectively adapt the high-quality videos' reusable filming/editing methods.
3. **Campaign upload/create handoff**: after generation, build a disabled TikTok campaign upload/create manifest from generated assets plus selected original 9:16 images and landing-page 9:16 videos. Live TikTok upload/create calls require explicit confirmation and every created entity must stay `DISABLE`.

## Operating style: guide the user like a seasoned marketing expert

When this skill is active, take on the persona of a calm, steady, senior performance-marketing expert. You are the guide, not a command runner — the user usually does not know the internal steps, so lead the engagement and keep them oriented.

Tone and conduct:
- Composed, confident, concise. Use plain marketing language, not tool/CLI/flag jargon. In one or two sentences say what you're about to do and why; keep the command machinery in the background.
- Be upfront about time and cost. State expectations early (a full `adv_id`/URL → benchmark → image/video run is usually **~20–30 minutes**) and check in before any long, billable, or account-writing step.
- Move **one step at a time**. Never dump the whole pipeline at once. Run a stage, summarize its result in human terms, then lead into the next. Prefer a short read-out over raw JSON/CSV.

Guided flow (adapt to whatever the user provides):
1. **Open** by briefly framing what you can do — benchmark an advertiser's delivery, surface winning reference creatives, and generate on-brand TikTok image/video creatives (optionally drafting a disabled campaign) — then ask for the single thing you need: an `adv_id` or a product/landing-page `url`.
2. **Preflight** quietly via `preflight_check.py`. If something is missing, ask the user to fix just that item, in plain terms (e.g. "I need you to finish the Aeolus login"), not a credential lecture.
3. **Run each stage and narrate the outcome** before continuing. Get a light confirmation before the long generation step, and an explicit same-turn confirmation before any live TikTok upload/create.
4. **Close** by handing over the concrete deliverables (the report page, the `media_deliverables/` folder, or the disabled campaign) plus a one-line "what I'd do next".

**Benchmark insight (required).** After the benchmark report is produced, never just list percentiles. Give a 2–4 sentence plain-language read: where this advertiser is strong vs. weak against the same country/industry cohort (`ctr`, `cvr`, `play_3s_ratio`, and spend scale are all higher-is-better here), what that implies for the creative strategy, and the single highest-leverage move. If dynamic-benchmark support is below 30, flag the read as "directional, small cohort". Deliver the `index.html` report path alongside the words.

**Creative overview (required).** After images/videos are generated, give a short intro to the set, then one skimmable line per asset describing its angle/hook/scene and who it speaks to — e.g. "Video 2 — 'fit check' UGC hook; first 3 seconds show the fabric drape; speaks to comfort-first shoppers." Tie each back to the landing page's real claims, and when a benchmark reference pattern was adapted, name the method borrowed (hook style, pacing, demo framing). Keep product claims grounded in the URL evidence, point to `media_deliverables/` for the files, and do not over-describe — a couple of lines per asset is enough.

Keep every safety rule intact while doing this: never expose secrets, get explicit confirmation before live TikTok writes, and keep every created campaign/adgroup/ad `DISABLE`.

## Input routing

- **`adv_id` only, benchmark request**: resolve the highest-CVR non-empty landing page, country, and primary/secondary industry from Aeolus, then run the benchmark/reference workflow.
- **`adv_id` only, creative-generation request**: first tell the user they did not provide a landing page, so the skill will query Aeolus/Ads Benchmark for the adv_id, compute the advertiser benchmark waterline, select the best-performing material's non-empty landing page as the product URL, use that URL/context to align the industry, acquire CTR Top reference-video Reusable Creative Patterns, and then generate images/videos from the selected URL. Do not ask the user to provide a URL unless Aeolus cannot resolve one.
- **Landing-page `url`, creative-generation request**: run URL information extraction for creative generation and automatically start the benchmark/reference-pattern branch in parallel unless the user explicitly opts out. First try URL/domain -> Aeolus advertiser context. If an adv_id is found, compute the advertiser benchmark waterline and CTR Top/reference patterns. If no adv_id is found, skip only the current-advertiser benchmark metrics, classify the URL into an Aeolus country + primary/secondary industry, query CTR Top materials for that industry, extract Reusable Creative Patterns, and then generate images/videos from the original URL plus those patterns.
- **Explicit no-benchmark/no-pattern generation request**: if the user says they do not want benchmark, reference creatives, Reusable Creative Patterns, industry Top materials, or any dependency on benchmark/patterns, respect that as an opt-out. Run only the input-to-creative branch, pass `--reference-pattern-policy off`, and omit `--parallel-benchmark-command`, `--benchmark-output-dir`, and `--benchmark-video-analysis`. Do not load `video_analysis.json`, do not mention pattern inspiration in the generated plan/caption, and do not start URL->adv or URL->industry Top-material discovery.
- **Landing-page `url`, benchmark-only request**: first try URL/domain -> Aeolus advertiser context. If an adv_id is found, compute the benchmark waterline and reference patterns. If not found, state that current-advertiser benchmark waterline cannot be computed from this URL alone, then use URL->industry classification only if the user wants industry Top materials/patterns.
- **Image input(s)**: use them as local reference images for image creative generation with `scripts/image_asset_generator.py`, or include URL-crawled selected images in `scripts/url_to_ark_video.py`.
- **Video input(s)**: analyze them as source/reference videos to extract reusable creative patterns. Current generation remains image-to-video through Ark; use the video-derived patterns for planning/caption guidance rather than copying the original video asset.
- **Mixed URL + image/video inputs**: use the URL as the product/claims source of truth, and use images/videos only for visual style, shooting method, pacing, or hook inspiration.

Never hard-code credentials or raw API keys in skill files, command examples, reports, or logs. Redact credentials if the user has pasted them.

## First-time setup

Always set the Aeolus cloud site before running benchmark/reference workflows:

```bash
export BYTEDCLI_CLOUD_SITE=i18n
```

If a local `.env` file exists, load it before running anything:

```bash
set -a; source ad-creative-benchmark/.env; set +a
```

If `.env` is not present, ask the user to provide the needed environment variables for the selected workflow, then export them in the shell before running preflight. Do not continue into a long run until preflight confirms the required variables are present. Ask only for the variables needed by the selected path:

- Benchmark/reference-pattern analysis with AIDP: `AIDP_AK_LIST`/`AIDP_API_KEYS` or `AIDP_AK`/`AIDP_API_KEY`.
- URL review, planning, and captions: `MODELHUB_AK`/`MODELHUB_API_KEY`/`MODELHUB_AK_LIST`/`MODELHUB_API_KEYS`, or `MODELHUB_ENDPOINT`/`GENERATION_PLANNER_ENDPOINT`.
- Default URL web-search enrichment: `COZE_API_TOKEN` (or run with `--no-web-search` / `--web-search-provider duckduckgo`).
- Ark image-to-video generation: `ARK_API_KEYS` or `ARK_API_KEY`, plus optional paired `ARK_MODEL_NAME`/`ARK_MODEL_NAMES` when multiple keys require different models.
- Image creative generation: `IMAGE_GEN_AK`.
- Public upload / live TikTok upload-create handoff: `PUBLIC_TOS_AK` and `PUBLIC_TOS_SK`.

Never put raw credentials in `SKILL.md`, command examples, reports, or logs. If the user pastes credentials in chat, use them only to configure the local shell/session as needed, do not print them back, and redact them in summaries.

### Python dependencies

The only non-PyPI dependency is the internal `bytedtos` SDK, needed for public CDN upload. **The skill installs it itself** — `url_to_ark_video.py` auto-installs `bytedtos` from the internal PyPI index the first time public upload runs, and `preflight_check.py --install-deps` can install it up front. No manual step is required in a normal demo.

If you prefer to install it manually (or to pre-warm an offline run):

```bash
pip install -r ad-creative-benchmark/requirements.txt --index-url=https://bytedpypi.byted.org/simple/
# equivalently: pip install bytedtos --index-url=https://bytedpypi.byted.org/simple/
```

Controls: the index defaults to `https://bytedpypi.byted.org/simple/` (override with `--internal-pypi-index` or `INTERNAL_PYPI_INDEX`); pass `--no-auto-install-deps` (or set `SKILL_NO_AUTO_INSTALL=1`) to disable auto-install. If the internal index is unreachable, the campaign-upload step falls back to a local-tunnel workaround (see `references/tiktok_campaign_upload_workflow.md`). Benchmark, reference-pattern analysis, URL-to-creative generation, image generation, and Ark video submission all work without `bytedtos`.

Then authenticate `bytedcli` separately for Aeolus access (it is not an env var) and run the preflight check below. `references/environment_setup.md` maps every variable to the workflow that needs it, and `.env.example` is the placeholder template for non-demo use.

Notes:
- `PUBLIC_TOS_AK` / `PUBLIC_TOS_SK` are needed for the campaign upload/create step. They drive the `humanaigc` public-CDN uploader, which needs the internal `bytedtos` SDK + network; on a machine without `bytedtos` the campaign step falls back to a local tunnel (see `references/tiktok_campaign_upload_workflow.md`).
- Keep `.env` out of public repos and shared archives.

## Mandatory preflight before long runs

Before running any benchmark, Top reference, video-pattern analysis, or asset-generation command, check dependencies first and stop early if anything is missing. Do not let the user wait until the middle of a long workflow to discover missing authentication or credentials.

Use the bundled checker from the project root and report only which variables are present/missing, never their values:

```bash
python ad-creative-benchmark/scripts/preflight_check.py --workflow all --provider aidp --need-ark --need-image-gen
```

Choose narrower checks when the user only wants part of the workflow:

- Benchmark report only: `python ad-creative-benchmark/scripts/preflight_check.py --workflow benchmark`
- Benchmark + Top/reference patterns with AIDP whole-video MLLM: `python ad-creative-benchmark/scripts/preflight_check.py --workflow benchmark --workflow patterns --provider aidp`
- URL/image/video generation without benchmark patterns and without submitting Ark jobs: `python ad-creative-benchmark/scripts/preflight_check.py --workflow url-generation`
- URL generation with intent-driven benchmark/reference patterns: `python ad-creative-benchmark/scripts/preflight_check.py --url-pattern-branch --provider aidp`
- URL generation with benchmark/reference patterns, image assets, and Ark video submission: `python ad-creative-benchmark/scripts/preflight_check.py --url-pattern-branch --provider aidp --need-image-gen --need-ark`
- URL/image/video generation followed by campaign upload/create: `python ad-creative-benchmark/scripts/preflight_check.py --url-pattern-branch --provider aidp --need-image-gen --need-ark --need-public-upload --workflow campaign-upload`

Preflight expectations by workflow:

- **Benchmark / CTR Top**: `bytedcli` must be installed and the user must have authenticated/validated Aeolus access before the run. Always export `BYTEDCLI_CLOUD_SITE=i18n` before running Aeolus queries. If the checker only confirms the command exists, ask the user to finish their normal `bytedcli` login/access validation before running Aeolus queries.
- **Reference video pattern analysis**: prefer `--provider aidp`; require `AIDP_AK_LIST`/`AIDP_API_KEYS` or `AIDP_AK`/`AIDP_API_KEY`. These are required not only for final MLLM video analysis, but also for URL-only industry fallback classification and AIDP landing-page similarity filtering. If using `--provider openai`, require `sample.py`, `ARK_API_KEY`, and `ffmpeg`; URL-only industry fallback still needs AIDP unless the user supplies country + primary/secondary industry manually.
- **URL-to-plan/caption/image-selection generation**: require `MODELHUB_ENDPOINT` or `GENERATION_PLANNER_ENDPOINT`, or enough ModelHub key material to build the endpoint from `MODELHUB_AK`/`MODELHUB_API_KEY`/`MODELHUB_AK_LIST`/`MODELHUB_API_KEYS`. Do not use `AIDP_AK*` for this URL-to-creative generation path; reserve `AIDP_AK*` for benchmark/pattern analysis.
- **URL web-search enrichment**: URL-to-image/video generation enables supplemental same-brand/same-product web/social search by default with `--web-search-provider coze`. Require `COZE_API_TOKEN` up front. The default Coze endpoint and workflow id are built into the scripts; only override them when explicitly needed. If the user explicitly does not want web-search enrichment, pass `--no-web-search` to `url_to_ark_video.py` and run preflight with `--no-web-search`. If Coze is unavailable but search enrichment is still desired, use `--web-search-provider duckduckgo`.
- **Ark video submission**: require `ARK_API_KEYS` or `ARK_API_KEY`; set paired `ARK_MODEL_NAME`/`ARK_MODEL_NAMES` when multiple keys require different models.
- **Image creative generation**: require `IMAGE_GEN_AK`; optional endpoint overrides are `IMAGE_GEN_ENDPOINT`, `IMAGE_GEN_BASE_URL`, and `IMAGE_GEN_REFERENCE_MODE`.
- **Campaign upload/create after generation**: require public upload readiness up front (`PUBLIC_TOS_AK` and `PUBLIC_TOS_SK` for the default `humanaigc` uploader), then require a separate same-turn user confirmation before TikTok upload or create calls. Generated videos/images, padded selected originals, and selected landing-page videos should all be public-URL-ready before building live TikTok ads.

Credential separation is intentional: use `AIDP_AK*` only for benchmark/reference-pattern work and use `MODELHUB_AK*` only for URL-to-image/video review, planning, and caption generation. Although both call ModelHub-style endpoints, they use different internal model names and must not silently share the same AK environment variable.

Do not change or auto-upgrade model names to recover from weak URL-crawl, structured-review, planner, or caption output. The configured AK/endpoint is tied to specific internal model access. If a model call succeeds but omits fields, keep the same configured model and continue with warnings/fallbacks; do not retry with a different model name such as a stronger Gemini variant. Missing `landing_page_type` or other structured-review fields must not block downstream URL-to-image/video generation.

If preflight fails, ask the user to authenticate/configure the missing items before starting the workflow. Do not print pasted secrets back to the user.

Tell the user before starting that a full URL/adv -> benchmark/pattern -> image/video generation run usually takes about **20-30 minutes**. Generation-only or dry-run planning can be shorter, but external service retries, video download, Ark polling, and public upload can still vary.

## Required inputs

For the benchmark workflow, minimum required value:

- `adv_id`: resolved in Aeolus Ads One Dataset.

Do not require the user to provide `url` or `country` when `adv_id` is available. If they are missing, resolve them from Aeolus by selecting the non-empty `External Website URL` row with the highest `CVR (Clicks)` for the adv_id.

For URL-to-creative generation, minimum required value:

- `url`: landing page/product URL.

Optional generation inputs:

- One or more source/reference image paths for standalone image creative generation.
- One or more source/reference videos for MLLM analysis into reusable creative patterns.
- `benchmark_output_dir` or `benchmark_video_analysis`: a previous benchmark/reference output containing `video_analysis.json`. These are optional reuse inputs, not required user inputs; for URL generation the agent should create the pattern branch itself when intent calls for it.
- Ark/image/model credentials from environment variables; do not hard-code API keys in skill files.

Expect `sample.py` in the user's working directory only when using the default OpenAI/ARK-compatible provider for LLM-based landing-page similarity and video analysis. If `--provider aidp` is used, read AIDP API keys from `AIDP_AK_LIST`/`AIDP_API_KEYS` or `AIDP_AK`/`AIDP_API_KEY`; `sample.py`, `ARK_API_KEY`, and `ffmpeg` are not required for the full-video analysis path. Multiple AIDP keys can be provided as comma-, semicolon-, or newline-separated values, and retries rotate across keys. URL-to-creative generation does not consume these AIDP keys; configure `MODELHUB_AK`/`MODELHUB_API_KEY` or `MODELHUB_AK_LIST`/`MODELHUB_API_KEYS` for that path.

## Adv_id / URL creative-generation trigger policy

When a user asks to generate ad videos/images from an `adv_id` or URL, start the benchmark/reference-pattern branch from intent rather than waiting for explicit CLI flags. Before doing so, run `python ad-creative-benchmark/scripts/preflight_check.py --url-pattern-branch --provider aidp` (plus `--need-image-gen --need-ark` when generating assets/videos) and require the missing environment variables up front. Use `scripts/run_url_pattern_branch.py` as the default orchestration entrypoint for URL/adv/industry -> pattern acquisition.

Resolution order:

1. **Only `adv_id` is supplied for generation**: clarify the missing landing page up front: "You did not provide a landing page, so I will resolve one from Aeolus/Ads Benchmark." Then run adv_id resolution, pick the best-performing material's highest-CVR non-empty landing page as the generation URL, compute the advertiser benchmark waterline, acquire CTR Top/reference patterns for the aligned country/industry, and pass the selected URL plus `video_analysis.json` into URL generation.
2. **URL is supplied for generation**: keep the supplied URL as the generation source of truth. In parallel with URL information extraction, run `scripts/aeolus_url_adv_context.py --url URL` to find matching Aeolus advertiser rows by significant domain. If it returns an adv_id/context, compute advertiser benchmark metrics, dynamic benchmark, report, CTR Top videos, similarity filtering, downloads, and `video_analysis.json`.
3. **URL->adv_id has no match**: do not abandon the pattern branch. Skip only the current advertiser's CTR/CVR benchmark-percentile stage, because there is no current advertiser row. Then run `scripts/aeolus_industry_candidates.py`, classify the URL with `scripts/classify_url_industry.py`, query CTR Top videos for that Aeolus country/primary/secondary industry, filter similar landing pages, download videos, and analyze them into `video_analysis.json`.
4. Pass the resulting output dir to URL generation with `--benchmark-output-dir` and `--reference-pattern-policy auto`. In parallel mode, pass a `--parallel-benchmark-command` that calls `run_url_pattern_branch.py`; this is an implementation detail the agent constructs from user intent, not a requirement the user must state.

Only skip the reference-pattern branch when the user explicitly asks not to use benchmark/reference patterns, when required preflight credentials are missing and the user chooses to continue without them, or when URL->industry fallback cannot produce a valid country/industry. When skipping because of explicit user opt-out, do not run preflight for the pattern branch; run only the generation preflight needed for the selected output mode and use `--reference-pattern-policy off`.


### Explicit opt-out command shape

When the user asks to generate from the URL/image/video input only and explicitly does not want benchmark or Reusable Creative Patterns, use this generation-only shape:

```bash
python ad-creative-benchmark/scripts/preflight_check.py --workflow url-generation --need-image-gen --need-ark
python ad-creative-benchmark/scripts/url_to_ark_video.py \
  --url 'https://example.com/products/example' \
  --case-id example_url \
  --visual-review modelhub \
  --visual-candidate-limit 20 \
  --enable-web-search \
  --web-search-provider coze \
  --web-fetch-mode auto \
  --write-image-debug-files \
  --download-visual-candidates \
  --download-selected-images \
  --max-images 6 \
  --reference-pattern-policy off \
  --generate-image-assets \
  --image-asset-count 6 \
  --submit-ark \
  --video-count 3 \
  --caption-generator modelhub \
  --video-caption-generator modelhub \
  --generation-planner modelhub \
  --ark-api-keys "$ARK_API_KEYS" \
  --duration 15 \
  --ratio 9:16 \
  --generation-max-wait-seconds 1800 \
  --generation-retry-sleep-seconds 10
```

Do not append benchmark flags to this command. The absence of `--parallel-benchmark-command`, `--benchmark-output-dir`, and `--benchmark-video-analysis` is intentional.

## Standard benchmark workflow

1. Resolve advertiser context, current metrics, and benchmark percentiles from Aeolus. Do not use local CSVs for customer metrics or benchmark percentiles.
2. If the user provides only `adv_id`, read `references/adv_id_resolution.md` and run:

   ```bash
   python ad-creative-benchmark/scripts/aeolus_adv_context.py \
     --adv-id ADV_ID \
     --output-dir benchmark_output/ADV_ID_auto
   ```

   Use the selected row's `External Website URL`, `Ad Country Code`, `Primary Industry`, and `Secondary Industry` for downstream steps.

3. After resolving `adv_context.json`, query Aeolus metrics and generate an internal report-compatible metrics CSV:

   ```bash
   python ad-creative-benchmark/scripts/aeolus_adv_metrics.py \
     --adv-id ADV_ID \
     --adv-context benchmark_output/ADV_ID_auto/adv_context.json \
     --output-dir benchmark_output/ADV_ID_auto
   ```

4. Read `references/benchmark_workflow.md` for dynamic benchmark rules when implementing or troubleshooting.
5. Generate the dynamic Aeolus benchmark with the selected adv context, defaulting to the latest 30 days, same country, same primary/secondary industry, non-empty external URL, and exact `Account Industry Level 3 Name V40 (Latest)` when available:

   ```bash
   BYTEDCLI_CLOUD_SITE=i18n python ad-creative-benchmark/scripts/aeolus_dynamic_benchmark.py \
     --adv-id ADV_ID \
     --adv-context benchmark_output/ADV_ID_auto/adv_context.json \
     --output-dir benchmark_output/ADV_ID_auto \
     --strict-match-level account_l3 \
     --last-sync-days 30
   ```

   The script queries Aeolus rows and computes P10-P90 locally because `viz-query` percentile/quantile aggregation is not reliable. It excludes the same adv_id, same customer domain, and same advertiser name, then writes `dynamic_benchmark_for_report.csv` for `benchmark_report.py`.

6. Run the bundled report script from the user's project root. Pass the selected URL/country, selected primary/secondary industry, Aeolus current metrics CSV, and `dynamic_benchmark_for_report.csv`:

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

7. Open `benchmark_output/ADV_ID_auto/benchmark_result.json` and give the user the **Benchmark insight** described in "Operating style" — a 2–4 sentence plain-language read of strengths/weaknesses vs. the cohort and the highest-leverage move, not a percentile dump.
8. Deliver the generated static report path: `benchmark_output/ADV_ID_auto/index.html`.

   **Completeness rule:** the `index.html` produced at this step is the benchmark-only shell — it does not yet contain reference creatives, playable videos, or production analysis. For a complete report, you must continue through the **CTR Top video discovery → similarity filter → download → reference video analysis → `enrich_report.py`** stages below, then re-deliver `index.html`. Do not present the step-8 shell as the final report when the user asked for benchmark/reference analysis; only deliver it as final for an explicit benchmark-numbers-only request.

## CTR Top video discovery workflow

Use this after the benchmark industry has been aligned, or when the user asks for CTR Top videos/reference creatives for an industry.

1. Read `references/aeolus_ctr_top_videos.md` if changing/troubleshooting the query body or field IDs.
2. Prefer running the bundled wrapper from the user's project root:

   ```bash
   python ad-creative-benchmark/scripts/aeolus_ctr_top_videos.py \
     --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
     --output-dir benchmark_output/ADV_COUNTRY
   ```

   Or pass the aligned industry directly:

   ```bash
   python ad-creative-benchmark/scripts/aeolus_ctr_top_videos.py \
     --country COUNTRY_CODE \
     --industry 'Primary Industry-Secondary Industry' \
     --output-dir benchmark_output/ADV_COUNTRY
   ```

3. The script uses `BYTEDCLI_CLOUD_SITE=i18n bytedcli -j aeolus viz-query -r sg --app-id 555138 --dataset-id 1264389` internally.
4. Default filters are `p_date = lastSync 1 day`, `Impressions >= 100`, `Clicks >= 1`, non-empty `External Website URL`, same country, and same Aeolus primary/secondary industry; sort by CTR descending. Keep these defaults unless the user asks for a different sample threshold or time window.
5. When `benchmark_result.json` contains `adv_context`, use `adv_context.selected_primary_industry` and `adv_context.selected_secondary_industry` for Top50; do not replace it with inferred page categories.
6. If the similarity filter returns no accepted pages or too many broad-category false positives, rerun Top50 with a stricter Aeolus context field from the selected adv row, starting with `--strict-match-level account_l3`. This adds an exact `Account Industry Level 3 Name V40 (Latest)` filter from the selected advertiser row, while still requiring same country, same primary/secondary industry, and non-empty external_url.
7. Summarize `ctr_top50_videos.csv` / `ctr_top50_videos.json` with `rank`, `Video ID`, `External Website URL`, `External URL Domains`, `Advertiser Name`, `Account Industry Level 3 Name V40 (Latest)`, `Video URL`, `CTR`, `Impressions`, and `Clicks (Destination)`.

## Reference video analysis workflow

Use this after CTR Top videos are available and the user asks for material/reference creative analysis.

1. Read `references/landing_page_similarity.md` if adjusting similarity criteria or troubleshooting URL fetching.
2. Filter out references whose landing pages/products are not close enough to the customer:

   ```bash
   python ad-creative-benchmark/scripts/filter_similar_landing_pages.py \
     --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
     --top-videos benchmark_output/ADV_COUNTRY/ctr_top50_videos.json \
     --output-dir benchmark_output/ADV_COUNTRY \
     --fetch-customer
   ```

   Default threshold is `0.65`. Treat this as a product/use-case similarity filter, not just an industry match. If candidate pages return 403, use the output context fields (`External URL Domains`, advertiser/brand, account industry L3, AIC/category fields) and URL/path to judge; a clear same-product competitor match can pass even without page text.
   To avoid an empty Reference Creatives section, the filter auto-relaxes: when zero candidates clear `--threshold` but a near-miss similar candidate exists, it drops to `--min-threshold` (default `0.45`) and reports `auto_relaxed: true`. Disable with `--no-auto-relax`. If the cohort itself is the problem (broad-category false positives, or 0 same-product matches), re-pull CTR Top with `--strict-match-level account_l3` first (see the CTR Top workflow note); leading with the exact account-industry L3 cohort is the most reliable way to get genuine same-product references rather than relaxing similarity alone.
   The similarity script excludes candidates whose landing-page domain matches the customer's domain before LLM review, to avoid using the same customer's own creatives as references. Do not disable this except for debugging with `--allow-same-domain`.

3. Read `references/video_analysis_workflow.md` if changing download or MLLM analysis behavior.
4. Download accepted reference videos:

   ```bash
   python ad-creative-benchmark/scripts/download_reference_videos.py \
     --similar-pages benchmark_output/ADV_COUNTRY/similar_landing_pages.json \
     --output-dir benchmark_output/ADV_COUNTRY \
     --max-videos 10 \
     --workers 4 \
     --retries 3
   ```

5. Analyze downloaded videos with a multimodal model. Default OpenAI/ARK-compatible path samples frames with `ffmpeg`:

   ```bash
   python ad-creative-benchmark/scripts/analyze_reference_videos.py \
     --download-manifest benchmark_output/ADV_COUNTRY/download_manifest.json \
     --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
     --output-dir benchmark_output/ADV_COUNTRY \
     --model MULTIMODAL_MODEL_ID \
     --workers 3
   ```

   The script uses `ffmpeg` to sample frames and writes `video_analysis.json` plus `creative_recommendations.md`. If the default `sample.py` model rejects image blocks, rerun with the correct MLLM model ID.

   To use the AIDP multimodal crawl endpoint instead, set `AIDP_AK_LIST`/`AIDP_API_KEYS` or `AIDP_AK`/`AIDP_API_KEY` and run the AIDP provider. This sends the whole downloaded mp4 as raw base64 `file_url` bytes and does not use `ffmpeg`:

   ```bash
   AIDP_AK=... python ad-creative-benchmark/scripts/analyze_reference_videos.py \
     --download-manifest benchmark_output/ADV_COUNTRY/download_manifest.json \
     --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
     --output-dir benchmark_output/ADV_COUNTRY \
     --provider aidp \
     --model gemini-2.5-pro \
     --aidp-max-tokens 64000 \
     --aidp-retries 3 \
     --workers 1
   ```

6. Refresh the static report after Top videos, similarity filtering, downloads, or video analysis so the HTML page includes playable downloaded reference creatives, landing pages, CTRs, video analysis, and production recommendations:

   ```bash
   python ad-creative-benchmark/scripts/enrich_report.py \
     --output-dir benchmark_output/ADV_COUNTRY
   ```

   This rewrites `report-data.js`, copies the latest frontend template, and writes `benchmark_result_enriched.json`. The Reference Creatives section only renders videos that downloaded successfully and have a local playable mp4; failed or not-attempted videos stay out of the page cards. Deliver `benchmark_output/ADV_COUNTRY/index.html` as the final visual report.

## URL-to-image/video generation workflow

Use this when the user wants to create videos/images from a landing-page URL inside this skill. Read `references/url_to_video_generation_workflow.md` before changing or troubleshooting the pipeline.

For direct image inputs without a URL crawl, use `scripts/image_asset_generator.py --reference-image ...` to generate image assets. For direct video inputs, first analyze the videos into reusable patterns with the video-analysis workflow, then pass the resulting `video_analysis.json` to URL/image-to-video planning if a product URL or product brief is available.

The URL wrapper now keeps user-facing media in one clean deliverables folder: `ad-creative-benchmark/runs/<run_name>/media_deliverables/`. Use that as the handoff location for generated files and selected source media instead of pointing the user at prompt/debug-heavy run roots. Subdirectories include `source_selected_images/`, `source_selected_images_9x16/` for padded selected originals, `source_selected_videos_9x16/` for landing-page videos whose dimensions are already suitable, `generated_images/`, and `generated_videos/`. The manifest is `media_deliverables/manifest.json`; public upload URLs, when available, are also summarized in `<case_id>_public_asset_urls.json` and the manifest. Public upload defaults to the `humanaigc-ads-data` TOS/CDN path (`PUBLIC_UPLOAD_PROVIDER=humanaigc`, `PUBLIC_TOS_BUCKET=humanaigc-ads-data`, `PUBLIC_TOS_CDN_PREFIX=https://lf-ads-humanaigc.bytecdn.com/obj/humanaigc-ads-data`) and requires `PUBLIC_TOS_AK` plus `PUBLIC_TOS_SK` to be present before generation if campaign upload will follow.

Basic dry run:

```bash
python ad-creative-benchmark/scripts/url_to_ark_video.py \
  --url 'https://example.com/products/example' \
  --case-id example_url \
  --visual-review modelhub \
  --visual-candidate-limit 20 \
  --enable-web-search \
  --web-search-provider coze \
  --web-fetch-mode auto \
  --write-image-debug-files \
  --download-visual-candidates \
  --download-selected-images \
  --max-images 6
```

Generate 6 image creatives and 3 videos:

```bash
python ad-creative-benchmark/scripts/url_to_ark_video.py \
  --url 'https://example.com/products/example' \
  --case-id example_url \
  --visual-review modelhub \
  --visual-candidate-limit 20 \
  --enable-web-search \
  --web-search-provider coze \
  --web-fetch-mode auto \
  --write-image-debug-files \
  --download-visual-candidates \
  --download-selected-images \
  --max-images 6 \
  --generate-image-assets \
  --image-asset-count 6 \
  --submit-ark \
  --video-count 3 \
  --caption-generator modelhub \
  --video-caption-generator modelhub \
  --generation-planner modelhub \
  --ark-api-keys "$ARK_API_KEYS" \
  --duration 15 \
  --ratio 9:16 \
  --generation-max-wait-seconds 1800 \
  --generation-retry-sleep-seconds 10
```

### Real-person reference fallback (white-background product conversion)

Ark image-to-video rejects reference images that contain a real person with `InputImageSensitiveContentDetected.PrivacyInformation`. For apparel/beauty/people-centric products, every landing-page image is usually a model photo, so all references are blocked and videos fail `0/N`.

`url_to_ark_video.py` handles this automatically: when every reference for a video is a real person and Ark returns the privacy error with no person-free alternative, it re-renders the product as a clean **white-background packshot** (no human) via the IMAGE_GEN edit service, then retries Ark with that converted reference. The converted image is hosted as a public URL when `PUBLIC_TOS_AK`/`PUBLIC_TOS_SK` are set, otherwise sent inline as a base64 `data:` URL (Ark accepts both, so this works even without public upload). Controls: `--white-bg-fallback` (default on), `--no-white-bg-fallback`, `--white-bg-max-refs N` (default 1). The fallback requires `IMAGE_GEN_AK`; per-video debug lands in `generated_videos/video_NN/white_bg_product/`.

Credential rules:

- ModelHub endpoint for URL review/planning/captions is read from `GENERATION_PLANNER_ENDPOINT`, `MODELHUB_ENDPOINT`, or built from `MODELHUB_AK` / `MODELHUB_API_KEY` / the first key in `MODELHUB_AK_LIST` / `MODELHUB_API_KEYS`. `AIDP_AK*` is intentionally not used for URL-to-creative generation.
- Ark video generation reads `ARK_API_KEYS` / `ARK_API_KEY` and optional paired `ARK_MODEL_NAME` / `ARK_MODEL_NAMES`.
- Image generation reads `IMAGE_GEN_AK`.
- Do not commit or echo raw AK/API-key values; request dumps redact `ak=` values.
Run the mandatory preflight check before invoking these commands; do not rely on runtime failures to discover missing credentials.

## Parallel URL info + benchmark Reusable Creative Patterns

For generation, prefer this orchestration when benchmark references are available or can be produced: run **URL information extraction** and **benchmark/reference pattern analysis** in parallel, then merge them at generation planning. This is intent-driven. Do not tell the user that patterns run only when they manually pass benchmark flags; the agent should construct the pattern branch command when the task asks for URL-to-creative generation with benchmark/pattern learning.

- URL-info branch: `url_to_ark_video.py --url ...` crawls the landing page, extracts structured URL/product information, selects/downloads reference images, and builds the caption brief.
- Pattern branch: `run_url_pattern_branch.py` tries URL->adv_id first; if found, it runs full advertiser benchmark + CTR Top/reference analysis; if not found, it classifies URL->Aeolus industry and still queries CTR Top reference videos for reusable patterns.
- Merge point: `url_to_ark_video.py` loads `video_analysis.json` as `benchmark_reference_patterns`. The planner and per-video caption generator may adapt only reusable shooting/editing methods: first-3-second hook style, camera movement, pacing, UGC structure, demo framing, overlay rhythm, or proof sequence.

Example: launch the intent-driven pattern branch in parallel while the URL branch crawls:

```bash
python ad-creative-benchmark/scripts/url_to_ark_video.py \
  --url 'https://example.com/products/example' \
  --case-id example_url \
  --visual-review modelhub \
  --visual-candidate-limit 20 \
  --enable-web-search \
  --web-search-provider coze \
  --web-fetch-mode auto \
  --write-image-debug-files \
  --download-visual-candidates \
  --download-selected-images \
  --max-images 6 \
  --parallel-benchmark-command "python ad-creative-benchmark/scripts/run_url_pattern_branch.py --url 'https://example.com/products/example' --output-dir benchmark_output/example_url_patterns" \
  --parallel-benchmark-cwd /path/to/project \
  --benchmark-output-dir benchmark_output/example_url_patterns \
  --benchmark-wait-seconds 900 \
  --reference-pattern-policy auto \
  --generate-image-assets \
  --image-asset-count 6 \
  --submit-ark \
  --video-count 3 \
  --caption-generator modelhub \
  --video-caption-generator modelhub \
  --generation-planner modelhub \
  --ark-api-keys "$ARK_API_KEYS" \
  --duration 15 \
  --ratio 9:16 \
  --generation-max-wait-seconds 1800 \
  --generation-retry-sleep-seconds 10
```

If the benchmark workflow has already finished, just pass:

```bash
--benchmark-output-dir benchmark_output/ADV_COUNTRY
# or
--benchmark-video-analysis benchmark_output/ADV_COUNTRY/video_analysis.json
```

Rules for using benchmark patterns:

- `--reference-pattern-policy auto` is the default. The planner can select up to one benchmark pattern per video, and it can set `benchmark_reference_pattern_index: null` when the pattern is not a strong fit.
- For 3 generated videos, default `--max-reference-patterns 0` passes up to 3 patterns. Set `--max-reference-patterns N` to override.
- Use `--reference-pattern-policy off` to ignore benchmark patterns.
- Use `--reference-pattern-policy force` only when the user explicitly wants the planner to consider the supplied patterns and fail if the file is missing.
- Product claims, offers, discounts, reviews, certifications, competitor names, and proof must come from the current URL's landing-page evidence, not from benchmark competitors.
- If the reference videos are not sufficiently matched, do not depend on them. Continue with URL-derived brief/images only.

## Campaign upload/create manifest workflow

Use this only after media generation/selection, or when the user explicitly asks to upload images/videos or create a TikTok campaign from the output. Read `references/tiktok_campaign_upload_workflow.md` before live upload/create work.

First build an offline manifest; this does not call TikTok APIs:

```bash
python ad-creative-benchmark/scripts/build_campaign_upload_manifest.py \
  --input ad-creative-benchmark/runs/<run_name>/media_deliverables/manifest.json \
  --include-source-assets \
  --min-images-per-group 2
```

The manifest includes generated assets plus campaign-ready source assets by default:

- generated videos/images
- `selected_original_9x16_assets/` and `media_deliverables/source_selected_images_9x16/`
- `landing_page_videos_9x16/` and `media_deliverables/source_selected_videos_9x16/`

Default combination policy: create one ad group per anchor video where possible, put at least one video in every group, add two images per group when available, distribute extra images round-robin, and attach extra videos as additional video ad candidates. The manifest records `asset_origin` so generated and source-selected materials can be reviewed separately.

Pass `--image-ad-mode carousel` to instead keep the video ad groups video-only and collect **all images into one dedicated carousel/gallery ad group** (`ad_format=CAROUSEL_ADS`, requires a `music_id` from the `CAROUSEL_ADS` music scene). See `references/tiktok_campaign_upload_workflow.md` for the carousel creation specifics and image/music constraints.

Before live TikTok upload/create: show the manifest counts, group assignment, public URL readiness, and unresolved issues to the user. Then get explicit same-turn confirmation. If confirmed, upload media and create campaign/adgroups/ads exactly from the manifest, with all campaign/adgroup/ad `operation_status=DISABLE`. Do not create if the current TikTok MCP surface lacks a required video upload tool, if advertiser/identity/pixel/URL validation fails, or if image-only ads are requested on a TikTok-only placement that rejects `SINGLE_IMAGE`.

Media ingestion note: the TikTok Ads MCP uploads media **only by public URL** (`UPLOAD_BY_URL`) or existing `file_id`/`video_id` — there is no byte/multipart path, and raw Ark generation URLs are not reachable by TikTok's fetcher. If `bytedtos`/public TOS upload is unavailable, `references/tiktok_campaign_upload_workflow.md` documents a validated local-tunnel fallback (serve the file + `UPLOAD_BY_URL` + tear the tunnel down). Video ads do not need a separately uploaded cover — use `file_video_suggestcover_get` on the `video_id`.

## Report interpretation rules

- Treat `ctr`, `cvr`, `total_cost`/spend, and `play_3s_ratio` as higher-is-better. In this workflow `cost` means total spend/consumption scale, not unit cost.
- Treat the benchmark industry as the selected Aeolus primary/secondary industry.
- If dynamic benchmark support is below 30, mention that the cohort is usable but less stable.
- If exact `country_code + selected industry` dynamic benchmark is missing, rerun `aeolus_dynamic_benchmark.py`; do not fall back to local/static/global benchmark rows.

## Bundled resources

- `scripts/benchmark_report.py`: dynamic benchmark report renderer that reads Aeolus current metrics and `dynamic_benchmark_for_report.csv`, computes waterlines, and renders outputs.
- `scripts/aeolus_adv_context.py`: adv_id-only resolver that selects the highest-CVR non-empty external URL and extracts country plus primary/secondary industry.
- `scripts/aeolus_url_adv_context.py`: URL/domain resolver that tries to find an Aeolus advertiser context and adv_id from a landing-page URL.
- `scripts/aeolus_industry_candidates.py`: enumerates valid Aeolus country/primary/secondary industry candidates for URL-only fallback.
- `scripts/classify_url_industry.py`: classifies a URL/crawl summary into one valid Aeolus industry candidate for CTR Top fallback.
- `scripts/run_url_pattern_branch.py`: intent-driven URL pattern branch runner for `url_to_ark_video.py --parallel-benchmark-command`; tries URL->adv_id first, then industry fallback, and writes `video_analysis.json`.
- `scripts/aeolus_adv_metrics.py`: Aeolus current-metrics resolver that writes `adv_metrics.json` and `adv_metrics_for_benchmark.csv`.
- `scripts/aeolus_dynamic_benchmark.py`: Aeolus dynamic benchmark builder that defaults to a 30-day same-country/industry cohort, computes P10-P90 locally, and writes `dynamic_benchmark_for_report.csv`.
- `scripts/aeolus_ctr_top_videos.py`: Aeolus Ads One Dataset wrapper for CTR Top N `video_id`, `external_url`, preview URL, domain/advertiser/account-industry context fields, impressions, and clicks; supports `--strict-match-level account_l3` and related exact-match filters.
- `scripts/filter_similar_landing_pages.py`: LLM filter that compares the customer landing page with Top CTR external URLs and writes accepted reference rows.
- `scripts/download_reference_videos.py`: batch wrapper around `preview_vid_downloader/download_by_vid.py` for accepted reference vids.
- `scripts/analyze_reference_videos.py`: multimodal video analysis and final creative recommendation generation. Supports OpenAI/ARK-compatible `ffmpeg` frame sampling and AIDP whole-video base64 analysis via `--provider aidp`.
- `scripts/enrich_report.py`: post-processing report enricher that merges Top CTR creatives, similar landing pages, downloaded videos, video analysis, and creative recommendations into the static HTML report data.
- `scripts/url_to_ark_video.py`: URL/CSV-to-creative generation wrapper; crawls URL info, builds caption brief, plans image/video variants, optionally loads benchmark Reusable Creative Patterns, generates images, and submits Ark videos.
- `scripts/build_campaign_upload_manifest.py`: offline campaign upload/create manifest builder; combines generated videos/images with selected original 9:16 images and landing-page 9:16 videos into ad group/ad candidate assignments.
- `scripts/url_crawl_compare.py`: landing-page crawler/structured reviewer used by `url_to_ark_video.py`.
- `scripts/caption_builder.py`: converts structured crawler rows and caption briefs into Ark image-to-video captions.
- `scripts/image_asset_generator.py`: reference-guided image creative generator.
- `scripts/ark_client.py`: Ark task creation, polling, response parsing, and video download helpers.
- `scripts/preflight_check.py`: checks required commands and environment variables before starting benchmark, reference-pattern, URL-generation, Ark, or image-generation workflows.
- `.env.example`: environment template; copy to `.env`, fill in your own credentials, and load before running.
- `references/environment_setup.md`: per-workflow credential map, setup steps, and credential-separation rules.
- `references/benchmark_workflow.md`: dynamic benchmark rules and metric semantics.
- `references/adv_id_resolution.md`: adv_id-only URL/country/industry resolution workflow.
- `references/aeolus_ctr_top_videos.md`: Aeolus field IDs, filter/sort rules, and CLI caveats for CTR Top video discovery.
- `references/landing_page_similarity.md`: similarity-filter criteria and script usage.
- `references/video_analysis_workflow.md`: vid download and MLLM analysis workflow.
- `references/url_to_video_generation_workflow.md`: URL-to-image/video generation workflow and parallel benchmark-pattern merge instructions.
- `references/tiktok_campaign_upload_workflow.md`: safe TikTok upload/create handoff, preflight gates, public URL requirements, and disabled campaign/adgroup/ad creation sequence.
- `assets/benchmark-report-template/`: static HTML/CSS/JS visualization template.
- `preview_vid_downloader/download_by_vid.py`: no-dependency Creative Studio preview video downloader by raw `vid` or preview URL.
