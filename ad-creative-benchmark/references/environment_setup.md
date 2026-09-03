# Environment & credentials setup

Anyone using this skill must supply **their own** credentials. Secrets are never
stored in the skill. Provide them as environment variables — either exported in
your shell or via a local `.env` file (see `.env.example` at the skill root).

> Security rules (enforced throughout the skill): never hard-code or commit raw
> AK/API-key values into skill files, command examples, reports, or logs. If a
> key is ever pasted into a chat or file, treat it as compromised and rotate it.

## Quick start

1. Copy the template and fill in your own values:
   ```bash
   cp ad-creative-benchmark/.env.example ad-creative-benchmark/.env
   # edit ad-creative-benchmark/.env
   ```
2. Load it into your shell session (keep `.env` out of version control):
   ```bash
   set -a; source ad-creative-benchmark/.env; set +a
   ```
3. Authenticate `bytedcli` separately (Aeolus access is not an env var — use your
   normal `bytedcli` login/access-validation flow).
4. Verify everything before any long run:
   ```bash
   python ad-creative-benchmark/scripts/preflight_check.py \
     --workflow all --provider aidp --need-ark --need-image-gen
   ```
   The checker reports only present/missing — never the values.

## Variable map by workflow

You only need the variables for the workflow(s) you run.

| Variable | Required for | Notes |
|---|---|---|
| `bytedcli` (CLI auth) + `BYTEDCLI_CLOUD_SITE=i18n` | Benchmark, CTR Top discovery | Not a key — log in via `bytedcli`. Scripts default site to `i18n`. |
| `AIDP_AK_LIST` (or `AIDP_API_KEYS` / `AIDP_AK` / `AIDP_API_KEY`) | Benchmark/reference **pattern analysis**, URL→industry fallback, AIDP landing-page similarity | Multiple keys allowed (comma/semicolon/newline); retries rotate across them. **Do not** reuse for URL-to-creative generation. |
| `MODELHUB_AK` (or `MODELHUB_API_KEY` / `MODELHUB_AK_LIST` / `MODELHUB_API_KEYS`; or `MODELHUB_ENDPOINT` / `GENERATION_PLANNER_ENDPOINT`) | URL→creative **review, planning, captions** (`--*-generator modelhub`) | Intentionally separate from `AIDP_AK*` — different internal model access. Don't mix them. |
| `COZE_API_TOKEN` | URL web-search enrichment (default `--web-search-provider coze`) | Skip by passing `--no-web-search`; or use `--web-search-provider duckduckgo`. |
| `ARK_API_KEYS` (or `ARK_API_KEY`) + `ARK_MODEL_NAME` (or `ARK_MODEL_NAMES`) | Ark image-to-video submission | Keys comma-separated; pair each key with its model id when multiple keys need different models. |
| `IMAGE_GEN_AK` | Image creative generation | Optional: `IMAGE_GEN_ENDPOINT`, `IMAGE_GEN_BASE_URL`, `IMAGE_GEN_REFERENCE_MODE`. |
| `PUBLIC_TOS_AK` + `PUBLIC_TOS_SK` | Public upload before TikTok campaign upload/create | Default uploader `humanaigc`; both AK and SK required up front. |

## Credential separation (important)

`AIDP_AK*` and `MODELHUB_AK*` both call ModelHub-style endpoints but resolve to
**different internal model names** and must not share the same env var:

- `AIDP_AK*` → benchmark / reference-pattern (video) analysis only.
- `MODELHUB_AK*` → URL-to-image/video review, planning, and caption generation only.

Do not auto-upgrade or swap model names to recover from weak output; the
configured AK/endpoint is tied to specific model access.

## Narrower preflight examples

- Benchmark only: `preflight_check.py --workflow benchmark`
- Benchmark + patterns (AIDP): `preflight_check.py --workflow benchmark --workflow patterns --provider aidp`
- URL generation, no patterns, no Ark: `preflight_check.py --workflow url-generation`
- URL pattern branch: `preflight_check.py --url-pattern-branch --provider aidp`
- Full generation: `preflight_check.py --url-pattern-branch --provider aidp --need-image-gen --need-ark`
- Through campaign upload: add `--need-public-upload --workflow campaign-upload`
