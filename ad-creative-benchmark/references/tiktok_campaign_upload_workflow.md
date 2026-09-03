# TikTok campaign upload/create workflow

Read this reference when the user wants to upload generated/selected media to TikTok Ads or create a campaign/ad groups/ads after URL-to-creative generation.

## Safety contract

- Do not call any TikTok upload or create API until the user confirms the exact manifest in the same turn.
- Every campaign, ad group, and ad must be created with `operation_status=DISABLE`.
- Never enable, resume, or raise budget without a separate explicit confirmation.
- Save redacted request/response JSON under the run directory if any live write occurs. Do not persist raw tokens or secrets.
- If a required read-only gate fails, stop before writes; do not create a partial campaign.

## Build the offline manifest first

After generation, use the clean deliverables folder or run directory:

```bash
python ad-creative-benchmark/scripts/build_campaign_upload_manifest.py \
  --input ad-creative-benchmark/runs/<run_name>/media_deliverables/manifest.json \
  --include-source-assets \
  --min-images-per-group 2
```

`--include-source-assets` is on by default. It includes:

- generated videos from `generated_videos/` or `media_deliverables/generated_videos/`
- generated images from `generated_image_assets/` or `media_deliverables/generated_images/`
- padded selected original images from `selected_original_9x16_assets/` or `media_deliverables/source_selected_images_9x16/`
- selected landing-page videos from `landing_page_videos_9x16/` or `media_deliverables/source_selected_videos_9x16/`

Default grouping: one anchor video per ad group, two images per ad group when available, extra images distributed round-robin, extra videos attached round-robin as additional video ad candidates. If there are many videos, this creates more ad groups up to `--max-groups`; every group must have at least one video.

### Image grouping mode: `--image-ad-mode {distribute, carousel}`

- `distribute` (default): images become single-image ad candidates spread across the video ad groups. Note these need an image-capable placement; TikTok-only feed rejects `SINGLE_IMAGE`.
- `carousel`: the video ad groups stay video-only, and **all images are collected into one dedicated ad group** with a single `creative_type=carousel` ad candidate (a TikTok Standard Carousel / gallery ad). This is the "3 videos → 3 ad groups + N images → 1 carousel group" layout. The carousel group is marked `is_carousel: true` and does not require a video.

```bash
python ad-creative-benchmark/scripts/build_campaign_upload_manifest.py \
  --input ad-creative-benchmark/runs/<run>/media_deliverables/manifest.json \
  --generated-only --image-ad-mode carousel
```

### Creating a carousel (`CAROUSEL_ADS`) ad — validated specifics

- The ad uses `ad_format=CAROUSEL_ADS` with `image_ids` (2–35) and a **required `music_id`**.
- The `music_id` must come from the carousel scene: `file_music_get` with `music_scene=CAROUSEL_ADS` and a `search_type` (e.g. `SEARCH_BY_KEYWORD` with `filtering.keyword`, or `SEARCH_BY_RECOMMEND` with `filtering.image_urls`). System music returned by the default `CREATIVE_ASSET` scene (copyright `MUSIC_FORBID_VIDEO_ALLOW`) is rejected with `40002 Please select valid music for Carousel Ads`.
- Carousel image limits: JPG/JPEG/PNG, at most 1242×2340 (or 2340×1242), aspect ratio at most 9:20 / 20:9, and `is_carousel_usable=true` from `/file/image/ad/search/`. Generated 9:16 creatives qualify.
- Keep uploaded image files small. `file_image_ad_upload` has a ~10s request timeout; pushing multi-MB PNGs over a slow public tunnel times out with `40002 Failed to read the response body / context deadline exceeded`. Recompress to ~150–250 KB JPG (e.g. cap width to 1080) before upload.

## Public URL requirement

TikTok upload-by-URL requires public URLs. URL-to-creative generation uploads generated assets and campaign-ready source assets when public upload credentials are present:

- `PUBLIC_UPLOAD_PROVIDER=humanaigc` (default)
- `PUBLIC_TOS_AK`
- `PUBLIC_TOS_SK`
- optional: `PUBLIC_TOS_BUCKET`, `PUBLIC_TOS_ENDPOINT`, `PUBLIC_TOS_CDN_PREFIX`, `PUBLIC_UPLOAD_KEY_PREFIX`

Run this before a generation intended for campaign upload:

```bash
python ad-creative-benchmark/scripts/preflight_check.py \
  --workflow url-generation \
  --workflow campaign-upload \
  --need-image-gen \
  --need-ark \
  --need-public-upload
```

If public URLs are missing in `campaign_upload_manifest.json`, either upload the files to a public CDN first or use a TikTok MCP direct file-upload tool if the active session exposes one. Do not assume image upload can handle MP4.

### The TikTok Ads MCP ingests media only by URL (no byte upload)

Validated against the `tiktok_ads_manager` MCP: every upload tool — `file_video_ad_upload`, `file_image_ad_upload`, `file_temporarily_upload`, `file_music_upload`, `catalog_*`, `playable_save` — accepts only `UPLOAD_BY_URL` (a public URL) or an existing `file_id`/`video_id`. The `tool_execute` gateway is JSON-only, so there is **no multipart/base64 path to push local file bytes**. Therefore TikTok upload always needs a URL its own fetcher can reach.

Two gotchas observed:
- **Ark-generated video URLs are not TikTok-reachable.** The `ark-content-generations-*.tos-sg.tiktok-row.org` signed URLs return data to us but TikTok's open-API fetcher fails them (`40914`/`40903 Failed to fetch url data`), on both `http` and `https`. Do not feed raw Ark URLs to `file_video_ad_upload`.
- **The `humanaigc` CDN path needs `bytedtos`** (internal SDK, endpoint `tos-cn-north.byted.org`). On machines without it, `_public_upload_humanaigc` raises `ModuleNotFoundError: bytedtos` and no public URL is produced.

### Fallback when no public CDN is available: local tunnel

When `bytedtos`/public TOS is unavailable but the local media files exist, expose them through a temporary public tunnel and use `UPLOAD_BY_URL`. This was validated end to end (local white-bg MP4 → tunnel → TikTok Asset Library `video_id`).

```bash
# 1. serve the deliverables dir locally
python3 -m http.server 8765 --directory ad-creative-benchmark/runs/<run>/media_deliverables/generated_videos &
# 2. open a public https tunnel (cloudflared preferred; localtunnel works via npx)
npx --yes localtunnel --port 8765      # prints https://<sub>.loca.lt
```

Then, before handing a URL to TikTok:
- **Self-test the tunnel URL returns the raw media, not an interstitial.** `curl -s <url> | head -c 16` must show the file header (e.g. MP4 `ftyp`), `content-type` must be `video/mp4` / `image/*`. localtunnel may serve an HTML reminder page to browsers; a plain GET that returns HTML means TikTok will fail with `Failed to fetch url data`.
- Call `file_video_ad_upload` / `file_image_ad_upload` with `upload_type=UPLOAD_BY_URL` and the tunnel URL. On success the media is copied into the Asset Library and the returned `video_id`/`image_id` is persistent — the tunnel is no longer needed.
- **Tear the tunnel and local server down immediately after upload** (`kill`/`pkill`). It is an outward-facing exposure of local files; keep it alive only for the upload window, and only serve the specific media directory.

This tunnel path is a workaround, not the intended design. Prefer `bytedtos`/public CDN on an internal environment when available. For video ads you do not need to upload the creative cover separately: call `file_video_suggestcover_get` to get a cover `image_id` from the uploaded `video_id`.

## Read-only TikTok gates before writes

Before any upload/create call, verify all required context with read-only TikTok Ads tools available in the active session:

1. Advertiser is readable for the supplied `advertiser_id`.
2. URL verification succeeds for the landing page.
3. Identity list is non-empty and has a usable identity for TikTok placements; if not, either use non-TikTok image-capable placements where valid or stop.
4. Pixel exists and has a usable event when the campaign objective is website conversions. If no pixel exists, use a traffic/awareness objective or stop; do not invent pixel IDs.
5. Existing campaign/adgroup/ad list calls succeed, confirming write context is not permission-blocked.
6. Balance check if a balance tool is available. Even disabled entities should be treated as budget-risking writes.

## Live upload/create sequence after confirmation

Use the manifest's `groups` as the source of truth.

1. Upload images: image public URL -> TikTok image upload -> `image_id`.
2. Upload videos: video public URL or local file -> TikTok video upload -> `video_id`; if the current MCP surface has no video upload tool, stop and report that video upload is blocked.
3. Create one campaign with `operation_status=DISABLE`.
4. Create one ad group per manifest group, each with `operation_status=DISABLE`, minimum budget, resolved objective, placement, targeting, identity, pixel/optimization event where applicable.
5. Create ads from each group's `ad_candidates`, all with `operation_status=DISABLE`.
6. Verify campaign/adgroup/ad readbacks and confirm every created entity is still `DISABLE`.

Important placement caveat: TikTok-only feed placements may reject `SINGLE_IMAGE` ads. Image candidates need an image-capable placement/format such as Pangle/GAB or carousel; otherwise only create video ads for TikTok-only placements.
