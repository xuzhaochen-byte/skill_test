# URL to image/video generation workflow

Use this workflow when the user wants to turn a landing-page URL, URL-derived images, or explicit source/reference images into TikTok-ready image creatives and Ark image-to-video jobs from inside this skill. If the user supplies source/reference videos, analyze them into reusable creative patterns first, then feed those patterns into planning; do not treat video input as direct video-to-video generation.

The bundled wrapper is:

```bash
python ad-creative-benchmark/scripts/url_to_ark_video.py --help
```

It runs the following stages:

1. Crawl a raw URL or CSV with `scripts/url_crawl_compare.py`.
2. Build a structured caption brief from the cleaned landing-page/product evidence.
3. Download selected visual reference images.
4. Output 9:16 padded versions of selected original images when needed.
5. Download landing-page videos and keep only videos with suitable 9:16 dimensions.
6. Ask the generation planner for image-creative plans and video-variant plans.
7. Optionally generate image creatives.
8. Optionally submit multiple Ark video jobs in parallel.
9. Optionally upload generated image/video files to public URLs.
10. Write reviewable prompts, redacted requests, plans, captions, outputs, timing metrics, and one clean media deliverables directory under `ad-creative-benchmark/runs/` by default.

## Real-person reference fallback: white-background product conversion

Ark image-to-video rejects person-containing reference images with HTTP 400 `InputImageSensitiveContentDetected.PrivacyInformation`. Apparel/beauty/people-centric landing pages usually have only model photos, so the existing "switch to person-free reference" recovery has nothing to switch to and all videos fail.

`url_to_ark_video.py` adds an automatic recovery: when every reference for a video is a real person and Ark returns the privacy error, it calls `convert_video_refs_to_white_bg(...)`, which uses the IMAGE_GEN edit service (`generate_edit_with_retry`, which — unlike Ark — accepts person photos) to re-render the exact same product on a pure white background with no human (`WHITE_BG_PRODUCT_PROMPT`). The converted packshot is then used as the Ark reference and the job retries on the same key.

Hosting of the converted image:
- If `PUBLIC_TOS_AK`/`PUBLIC_TOS_SK` are set, it is uploaded and passed to Ark as a public CDN URL.
- Otherwise it is passed inline as a base64 `data:` URL. Ark accepts both forms, so the fallback works even without public-upload credentials.

Controls and behavior:
- `--white-bg-fallback` (default on) / `--no-white-bg-fallback`.
- `--white-bg-max-refs N` (default 1) — how many person references to convert per video.
- Requires `IMAGE_GEN_AK` (same key used for image creative generation). If absent, the fallback logs and is skipped.
- Conversion is attempted at most once per video; if Ark still rejects the converted image, the job falls back to the normal non-retryable handling.
- Per-video artifacts: `generated_videos/video_NN/white_bg_product/` (converted image + redacted result) and a `recovered_by: white_bg_product_conversion` entry in `attempts.json`.

Product fidelity rule: the conversion must preserve the exact garment/product (color, fabric, pattern, cut, logo) from the reference and must not invent colors, variants, or items. It only removes the person and changes the background.

## Objective-driven generation presets

`url_to_ark_video.py --objective {conversion,traffic,app_install,awareness,engagement}` sets creative shape from the campaign goal instead of the bare defaults (9:16 / 15s / 3 videos / 6 images). `conversion`, `traffic`, and `app_install` keep 15s × 3 videos with 6 images; `awareness` and `engagement` use shorter 9s × 4 videos with 8 images to test more hooks. The default is `none` (bare defaults). Presets only fill `--ratio`, `--duration`, `--video-count`, `--image-asset-count`, and `--max-images` when you did **not** pass those flags explicitly — any flag you set always wins. The objective can also be set via `CREATIVE_OBJECTIVE`. Pick the objective from the benchmark's resolved `objective_guess` when available.

## Supported inputs

- `--url`: crawl a single landing/product URL and use the URL as the source of product facts, claims, images, and offer evidence.
- `--raw-input`: crawl a CSV with a `raw_url` column.
- `--structured-input`: reuse an existing structured crawler CSV.
- Source/reference images: use URL-selected images through `url_to_ark_video.py`, or call `image_asset_generator.py --reference-image PATH` repeatedly for direct image inputs.
- Source/reference videos: use the benchmark/video-analysis workflow to produce `video_analysis.json`, then pass it with `--benchmark-video-analysis` or `--benchmark-output-dir` so the planner can reference reusable shooting/editing patterns.

## Required runtime credentials

Run preflight before any long URL-generation job. This catches missing LLM/image/video credentials before crawling, planning, or submitting tasks:

```bash
python ad-creative-benchmark/scripts/preflight_check.py --workflow url-generation
python ad-creative-benchmark/scripts/preflight_check.py --url-pattern-branch --provider aidp
python ad-creative-benchmark/scripts/preflight_check.py --url-pattern-branch --provider aidp --need-image-gen --need-ark
```

Use the first command for URL-only dry-run planning or for explicit user opt-out from benchmark/patterns. Use the second command whenever the run should automatically acquire benchmark/reference patterns. Use the third command when the same run also includes `--generate-image-assets` and `--submit-ark`. If any of `AIDP_AK_LIST`, `AIDP_API_KEYS`, `AIDP_AK`, or `AIDP_API_KEY` are missing for the pattern branch, stop and ask the user to export one of them before starting the long run. Separately, URL-to-creative generation needs `MODELHUB_AK`/`MODELHUB_API_KEY`/`MODELHUB_AK_LIST`/`MODELHUB_API_KEYS` or an explicit ModelHub endpoint; do not satisfy it with `AIDP_AK*`.

URL-to-image/video generation also enables supplemental web-search enrichment by default. The default provider is Coze, so preflight requires:

```bash
export COZE_API_TOKEN='...'
```

With `COZE_API_TOKEN` set, the default Coze endpoint (`https://api.coze.com/v1/workflow/stream_run`) and workflow id (`7647383585968422965`) are enough for the built-in same-brand/same-product web/social search. Use `--no-web-search` only when the user explicitly wants generation from the input URL/materials without web-search supplementation. Use `--web-search-provider duckduckgo` if Coze should not be used but search enrichment is still desired.

Before launching a full URL/adv -> benchmark/pattern -> image/video generation workflow, tell the user it generally takes **20-30 minutes**. Generation-only planning can be shorter, but image generation, Ark polling, retries, video downloads, and public uploads can still vary.

The URL-to-creative ModelHub endpoint is required for structured review, caption brief, caption generation, and generation planning. It is separate from AIDP benchmark/pattern keys because the internal model names differ. Provide it with one of:

```bash
export GENERATION_PLANNER_ENDPOINT='https://aidp-i18ntt-sg.tiktok-row.net/api/modelhub/online/v2/crawl?ak=...'
# or
export MODELHUB_ENDPOINT='https://aidp-i18ntt-sg.tiktok-row.net/api/modelhub/online/v2/crawl?ak=...'
# or let the script build the endpoint from the first configured key
export MODELHUB_AK='...'
```

You may provide multiple ModelHub keys for URL generation retry/rotation with either `MODELHUB_AK_LIST` or `MODELHUB_API_KEYS` (comma-, semicolon-, or newline-separated). `MODELHUB_AK` and `MODELHUB_API_KEY` are single-key fallbacks. Keep `AIDP_AK_LIST`/`AIDP_API_KEYS`/`AIDP_AK`/`AIDP_API_KEY` for benchmark, similarity filtering, URL industry fallback, and whole-video pattern analysis. Do not hard-code AKs into the skill files. Request dumps redact `ak=` values.

Do **not** switch or "upgrade" URL-to-creative model names when a URL crawl/planning/caption call returns weak or incomplete fields. The AK/endpoint is model-bound in this environment, so changing `--visual-model`, `--structured-review-model`, caption model, or generation-planner model can break authorization or route to the wrong internal model. Retry the same configured endpoint/model/key only. Missing structured-review fields such as `landing_page_type`, `objective_guess`, `business_type`, `conversion_action`, `clean_brand_name`, `clean_product_name`, or selling-point evidence are non-blocking: record warnings, use crawler/page fallbacks where available, and continue into caption planning, image generation, and video generation.

For video submission, provide Ark keys:

```bash
export ARK_API_KEYS='ark_key_1,ark_key_2,ark_key_3'
export ARK_MODEL_NAME='model_for_key_1,model_for_key_2,model_for_key_3'
```

`ARK_API_KEY` is accepted as a single-key fallback. `ARK_MODEL_NAMES` is accepted as an alias for multiple paired model names.

For image generation, provide:

```bash
export IMAGE_GEN_AK='...'
```

Do not write real credential values into this reference, command history intended for sharing, or final user-facing summaries.


## Explicit no-benchmark/no-pattern mode

If the user explicitly says they do not want benchmark/reference creatives/Reusable Creative Patterns/industry Top materials, run generation only. Do **not** start `run_url_pattern_branch.py`, do **not** pass a benchmark output path, do **not** load `video_analysis.json`, and do **not** let planner or caption prompts use benchmark patterns. The required control is `--reference-pattern-policy off`.

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

The following flags must stay absent in this mode: `--parallel-benchmark-command`, `--benchmark-output-dir`, and `--benchmark-video-analysis`.

## Single URL dry run

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
  --reference-pattern-policy off \
  --max-images 6
```

Without `--submit-ark` and `--generate-image-assets`, the run writes the structured crawl, caption brief, planner prompt/output, Ark caption, payload preview, selected images, and timing files only.

## Generate 6 images and 3 videos

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

The wrapper accepts multiple Ark keys and rotates tasks across them. Non-retryable create errors such as AccessDenied disable only that key for that job and continue with other configured targets. Generated images and videos are uploaded to public URLs by default when the configured uploader is available; use `--no-upload-generated-assets` to keep local-only outputs.


## Intent-driven adv_id/URL + benchmark pattern flow

For an end-to-end creative generation run from either `adv_id` or URL, the agent should start the benchmark/reference-pattern branch from user intent, not wait for the user to pass low-level benchmark flags. First run `preflight_check.py --url-pattern-branch --provider aidp` and require AIDP keys up front.

If the user supplies only `adv_id` for creative generation, explicitly clarify the routing: the user did not provide a landing page, so resolve the adv_id in Aeolus/Ads Benchmark, compute the advertiser benchmark waterline, select the best-performing material's highest-CVR non-empty landing page as the generation URL, acquire CTR Top/reference patterns for the aligned country/industry, then generate images/videos from that selected URL.

If the user supplies a URL for creative generation, keep that URL as the generation source of truth and launch these branches in parallel:

- Branch A: URL information crawl and creative generation input extraction (`url_to_ark_video.py --url ...`).
- Branch B: URL/adv/industry -> CTR Top reference materials -> reusable pattern analysis. The default orchestration entrypoint is `scripts/run_url_pattern_branch.py`, which eventually writes `video_analysis.json`.

Branch B resolution order:

1. If only `adv_id` is supplied, first resolve the best-performing non-empty landing page from Aeolus, then run the full advertiser benchmark + CTR Top/reference pattern workflow and use the resolved URL for generation.
2. If URL is supplied, first run URL/domain -> Aeolus advertiser context. When an adv_id is found, run full advertiser benchmark metrics, dynamic benchmark, report, CTR Top videos, similarity filtering, downloads, and `video_analysis.json`.
3. If URL->adv_id is not found, skip only the current advertiser benchmark metrics/percentiles. Still enumerate valid Aeolus industries, classify the URL into a country + primary/secondary industry, query CTR Top materials for that industry, filter similar landing pages, download videos, and analyze them into reusable patterns.

The two branches merge at generation planning. `url_to_ark_video.py` waits only at that point if `--benchmark-wait-seconds` is set, loads `Reusable Creative Patterns` from `video_analysis.json`, and passes them to the planner together with the URL-derived brief and selected product/reference images. If the user opted out of benchmark/patterns, this entire branch is skipped and the merge point receives an empty pattern list.

Recommended command shape for a single URL generation run:

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

If country is known, pass it through to reduce ambiguity:

```bash
--parallel-benchmark-command "python ad-creative-benchmark/scripts/run_url_pattern_branch.py --url 'https://example.com/products/example' --country FR --output-dir benchmark_output/example_url_patterns"
```

If an `adv_id` is known, pass it too:

```bash
--parallel-benchmark-command "python ad-creative-benchmark/scripts/run_url_pattern_branch.py --url 'https://example.com/products/example' --adv-id ADV_ID --output-dir benchmark_output/ADV_ID_auto"
```

The parallel command should write either:

- `--benchmark-output-dir .../video_analysis.json`, or
- the explicit `--benchmark-video-analysis /path/to/video_analysis.json`.

Logs are written to `parallel_benchmark_stdout.log`, `parallel_benchmark_stderr.log`, and `parallel_benchmark_process.json` under the run directory. With the default `--parallel-benchmark-fail-policy warn`, the run continues without benchmark patterns if the parallel branch fails, times out, or is not sufficiently matched. Use `--parallel-benchmark-fail-policy fail` only when benchmark patterns are mandatory.

## Optional benchmark reference-pattern integration

If this skill has already produced reference-video analysis in a benchmark output directory, pass that directory into the URL-to-video wrapper:

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
  --benchmark-output-dir benchmark_output/ADV_COUNTRY \
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

Equivalent explicit input:

```bash
--benchmark-video-analysis benchmark_output/ADV_COUNTRY/video_analysis.json
```

Behavior:

- The script compacts `video_analysis.json` into `benchmark_reference_patterns` using high-confidence analyzed videos.
- The generation planner receives those patterns together with the landing-page brief and selected reference images.
- For each video variant, the planner may choose at most one `benchmark_reference_pattern_index` and explain `benchmark_reference_usage`.
- When producing 3 videos, it can use up to 3 distinct benchmark patterns by default (`--max-reference-patterns 0` means use `--video-count`).
- The caption generator receives the selected pattern for that variant and may adapt only the reusable shooting/editing method, hook structure, pacing, camera moves, or edit rhythm.
- Product claims, offers, reviews, discounts, and proof must still come from the customer's landing page brief. Do not copy competitor scripts or visuals.
- If the benchmark videos are not sufficiently matched, `auto` mode allows the planner to set `benchmark_reference_pattern_index: null` and ignore them.

Controls:

```bash
--reference-pattern-policy auto|off|force
--max-reference-patterns 3
--reference-pattern-min-confidence 0.6
```

Use `off` if the user wants generation from the landing page only. Use `force` only for debugging or when the user explicitly wants every planner call to consider the supplied patterns; even in force mode, unsupported claims remain disallowed.

## Key outputs

Typical files under `ad-creative-benchmark/runs/<run_name>/`:

- `structured_crawl.csv`
- `<case_id>_caption_brief.json`
- `<case_id>_caption_generator_result.json`
- `<case_id>_benchmark_reference_patterns.json` when benchmark patterns are loaded
- `<case_id>_generation_planner_prompt.txt`
- `<case_id>_generation_plan.json`
- `<case_id>_generation_results.json`
- `selected_original_9x16_assets/selected_original_*_9x16_padded.jpg` for selected original images that needed padding
- `landing_page_videos_9x16/landing_video_*.mp4` for selected landing-page videos with suitable 9:16 dimensions
- `generated_image_assets/image_asset_*/generated_01.png`
- `generated_videos/video_*/video_*_15s.mp4`
- `<case_id>_public_asset_urls.json` when public upload succeeds
- `media_deliverables/manifest.json`
- `media_deliverables/source_selected_images/`
- `media_deliverables/source_selected_images_9x16/`
- `media_deliverables/source_selected_videos_9x16/`
- `media_deliverables/generated_images/`
- `media_deliverables/generated_videos/`
- `timing_metrics.json`

Use `media_deliverables/` as the final user-facing folder. It separates generated assets and selected source media from planner prompts, redacted requests, logs, and debug JSON files.

When handing these to the user, give the **Creative overview** described in SKILL.md "Operating style": a short intro to the set plus one skimmable line per asset (angle/hook/scene, who it speaks to, and any benchmark pattern adapted), grounded in the landing page's real claims. Read the per-asset `plan`/caption files (`<case_id>_generation_plan.json`, the per-video `plan.json`/`*_ark_caption.txt`) to describe each asset accurately rather than guessing.

Public upload uses the `humanaigc` provider by default so generated images/videos can be handed to TikTok upload-by-URL flows. Before running generation for campaign upload, check that `PUBLIC_TOS_AK` and `PUBLIC_TOS_SK` are set. Optional overrides: `PUBLIC_UPLOAD_SITE_PACKAGES`, `PUBLIC_TOS_BUCKET`, `PUBLIC_TOS_ENDPOINT`, `PUBLIC_TOS_CDN_PREFIX`, and `PUBLIC_UPLOAD_KEY_PREFIX`. Use `PUBLIC_UPLOAD_PROVIDER=sparrow` only as an explicit legacy fallback.

The wrapper uploads generated assets and campaign-ready source assets (`source_selected_images_9x16` and `source_selected_videos_9x16`) when public upload is enabled. After generation, build the offline campaign manifest with:

```bash
python ad-creative-benchmark/scripts/build_campaign_upload_manifest.py \
  --input ad-creative-benchmark/runs/<run_name>/media_deliverables/manifest.json \
  --include-source-assets \
  --min-images-per-group 2
```

The campaign manifest includes generated videos/images, padded selected original images from `selected_original_9x16_assets`, and landing-page videos from `landing_page_videos_9x16`. It proposes adgroup/ad combinations only; TikTok upload/create still needs explicit user confirmation and every created entity must be `DISABLE`.
