# Reference video download and MLLM analysis

Use after landing-page similarity filtering.

Run preflight before downloading/analyzing reference videos so missing model credentials or local tools are discovered up front:

```bash
python ad-creative-benchmark/scripts/preflight_check.py --workflow patterns --provider aidp
```

Use `--provider openai` only when `sample.py`, `ARK_API_KEY`, and `ffmpeg` are intentionally available.

## Download selected videos

```bash
python ad-creative-benchmark/scripts/download_reference_videos.py \
  --similar-pages benchmark_output/ADV_COUNTRY/similar_landing_pages.json \
  --output-dir benchmark_output/ADV_COUNTRY \
  --threshold 0.65 \
  --max-videos 10 \
  --workers 4
```

This script calls `preview_vid_downloader/download_by_vid.py` for each accepted `Video ID` and writes:

- `reference_videos/<vid>.mp4`
- `download_manifest.json`

The downloader uses the Creative Studio preview-page flow: `/api/preview/play_auth_token` -> VOD GetPlayInfo -> download `MainPlayUrl`. Prefer this bundled downloader; legacy files under `preview_vid_downloader/legacy_scripts/` are not the default path.

## Analyze videos with MLLM

### OpenAI/ARK-compatible provider with frame sampling

```bash
python ad-creative-benchmark/scripts/analyze_reference_videos.py \
  --download-manifest benchmark_output/ADV_COUNTRY/download_manifest.json \
  --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
  --output-dir benchmark_output/ADV_COUNTRY \
  --model MULTIMODAL_MODEL_ID \
  --workers 3
```

The default `openai` provider samples frames with `ffmpeg` and sends chronological image blocks to the `sample.py`-compatible OpenAI client in parallel. Use a multimodal ARK model. The default model parsed from `sample.py` may be text-only; if the API rejects image blocks, rerun with the correct MLLM model ID.

### AIDP provider with whole-video input

Use this path when the AIDP multimodal crawl endpoint should analyze the full video directly, or when `ffmpeg` / `ARK_API_KEY` / `sample.py` are unavailable:

```bash
AIDP_AK=... python ad-creative-benchmark/scripts/analyze_reference_videos.py \
  --download-manifest benchmark_output/ADV_COUNTRY/download_manifest.json \
  --benchmark-result benchmark_output/ADV_COUNTRY/benchmark_result.json \
  --output-dir benchmark_output/ADV_COUNTRY \
  --provider aidp \
  --model gemini-2.5-pro \
  --aidp-max-tokens 64000 \
  --aidp-timeout 900 \
  --aidp-retries 3 \
  --workers 1
```

The AIDP path sends the downloaded mp4 as one raw base64 `file_url` block with `mime_type=video/mp4`. Do not add a `data:video/mp4;base64,` prefix for this endpoint; the service maps `file_url` to Gemini inline bytes and expects raw base64.

AIDP keys can be supplied by `AIDP_AK_LIST`/`AIDP_API_KEYS` or `AIDP_AK`/`AIDP_API_KEY`. Use comma-, semicolon-, or newline-separated values for multiple keys. `--aidp-retries` rotates retry attempts across the configured keys.

Outputs:

- `video_analysis.json`: per-video first-3-second hook, structure, selling points, patterns, transferable ideas, and risks.
- `creative_recommendations.md`: final production recommendations for the customer.

## Refresh the visual report

After downloading or analyzing videos, update the report page:

```bash
python ad-creative-benchmark/scripts/enrich_report.py \
  --output-dir benchmark_output/ADV_COUNTRY
```

This adds industry reference creative cards, local video previews, landing-page links, CTR, similarity details, per-video MLLM analysis, and modular production recommendations to `index.html` via `report-data.js`. The page only shows reference creative cards for videos that downloaded successfully and have a local playable mp4.

Analyze these dimensions:

1. First 3 seconds: what is shown/said, hook type, overlay, immediate click driver.
2. Structure: hook -> problem/need -> product reveal/demo/proof -> CTA.
3. Selling points: benefits, emotional trigger, offer, proof, use case.
4. Transferability: adapt only elements suitable for the customer's landing page and benchmark gaps.
5. Risks: do not copy unrelated claims, low-confidence visual assumptions, or category-mismatched tactics.

Recommended defaults:

- `--max-videos 5` for first pass; increase only if needed.
- `--frame-fps 0.5 --max-frames 12 --frame-width 512` to control token/image cost.
- `--workers 3` for video analysis; lower it if the MLLM endpoint rate-limits.
- Keep `sampled_frames/` for auditability when reviewing MLLM outputs.
