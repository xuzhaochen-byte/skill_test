#!/usr/bin/env python3
"""Generate a TikTok/Ark video caption from URL crawler structured output.

This is the bridge between url_crawl_compare.py output and Ark i2v payloads.
It avoids hard-coding product-specific captions: product, selling points,
creator persona, voice style, scene plan, and image references are derived from
clean structured fields.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DEFAULT_INPUT = "/Users/bytedance/Documents/Codex/2026-06-15/files-mentioned-by-the-user-tiktok/outputs/url_crawl_compare_visual_text_rerun_full.csv"
DEFAULT_OUTPUT_DIR = "/Users/bytedance/Documents/Codex/2026-06-15/files-mentioned-by-the-user-tiktok/outputs/video_caption_briefs"


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_json_loads(text: Any, default: Any) -> Any:
    if text is None:
        return default
    text = str(text).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def uniq(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        item = norm(item)
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def load_row(path: Path, case_id: str | None, row_index: int = 0) -> Dict[str, str]:
    csv.field_size_limit(sys.maxsize)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if case_id:
        for row in rows:
            if norm(row.get("case_id")) == str(case_id):
                return row
        raise SystemExit(f"case_id not found: {case_id}")
    if row_index < 0 or row_index >= len(rows):
        raise SystemExit(f"row_index out of range: {row_index}")
    return rows[row_index]


LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
}


def normalize_language(lang: str) -> str:
    lang = norm(lang).lower().split("-", 1)[0].split("_", 1)[0]
    return lang if lang in LANGUAGE_NAMES else "en"


def row_language(row: Dict[str, str]) -> str:
    return normalize_language(row.get("language") or "en")


def title_case_brand(brand: str) -> str:
    brand = norm(brand)
    return brand if brand.isupper() else brand.title()


def _text_from_json_item(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "claim", "point", "value", "title", "evidence"):
            value = norm(item.get(key))
            if value:
                return value
        return norm(json.dumps(item, ensure_ascii=False))
    return norm(item)


def collect_claims(row: Dict[str, str]) -> List[str]:
    """Collect raw supported claim text without category-specific inference."""
    claims: List[str] = []
    for field in ("clean_selling_points_json", "selling_points_json"):
        for item in safe_json_loads(row.get(field), []):
            text = _text_from_json_item(item)
            if text:
                claims.append(text)
    for src in safe_json_loads(row.get("image_text_sources_json"), []):
        if not isinstance(src, dict):
            continue
        for item in src.get("key_claims") or []:
            text = _text_from_json_item(item)
            if text:
                claims.append(text)
        visible = norm(src.get("visible_text"))
        if visible:
            claims.append(visible[:180])
    return uniq([c for c in claims if 4 <= len(c) <= 220])[:12]


def build_brief_input(row: Dict[str, str], max_images: int) -> Dict[str, Any]:
    """Build raw LLM input for caption-brief generation.

    This function only gathers crawler/LLM-reviewed evidence. It does not
    infer product category, hooks, overlays, creator world, or scene drafts.
    """
    lang = row_language(row)
    brand = title_case_brand(row.get("clean_brand_name") or row.get("brand_name"))
    product = norm(row.get("clean_product_name") or row.get("product_name"))
    assets = select_assets(row, max_images=max_images)
    return {
        "case_id": row.get("case_id", ""),
        "language": lang,
        "language_name": LANGUAGE_NAMES.get(lang, "English"),
        "brand": brand,
        "product": product,
        "landing_page_type": row.get("landing_page_type", ""),
        "objective_guess": row.get("objective_guess", ""),
        "business_type": row.get("business_type", ""),
        "conversion_action": row.get("conversion_action", ""),
        "description": norm(row.get("clean_description") or row.get("description")),
        "price": row.get("price", ""),
        "ctas": safe_json_loads(row.get("cta_json"), []),
        "selling_points": safe_json_loads(row.get("clean_selling_points_json"), [])[:10],
        "pain_points": safe_json_loads(row.get("clean_pain_points_json"), [])[:8],
        "usage_scenarios": safe_json_loads(row.get("clean_usage_scenarios_json"), [])[:8],
        "creative_angles": safe_json_loads(row.get("clean_creative_angles_json"), [])[:8],
        "image_text_sources": safe_json_loads(row.get("image_text_sources_json"), [])[:8],
        "clean_web_sources": safe_json_loads(row.get("clean_web_sources_json"), [])[:8],
        "structured_review": safe_json_loads(row.get("structured_review_json"), {}),
        "supported_claims": collect_claims(row)[:12],
        "selected_images": assets,
    }



def select_assets(row: Dict[str, str], max_images: int) -> List[Dict[str, Any]]:
    creative = safe_json_loads(row.get("creative_ready_visual_images_json"), [])
    copy_assets = safe_json_loads(row.get("copy_source_images_json"), [])
    text_assets = safe_json_loads(row.get("text_heavy_images_json"), [])
    reviewed = safe_json_loads(row.get("clean_visual_assets_json"), [])

    by_url: Dict[str, Dict[str, Any]] = {}

    order_counter = 0

    def add(url: str, role: str, item: Dict[str, Any]) -> None:
        nonlocal order_counter
        url = norm(url)
        if not url or url in by_url:
            return
        order_counter += 1
        by_url[url] = {
            "url": url,
            "role": role,
            "bucket": item.get("bucket") or item.get("visual_review_bucket") or "unknown",
            "text_density": item.get("text_density") or "unknown",
            "source_order": order_counter,
        }

    # Prefer reviewed video-safe landing-page assets.
    for item in reviewed:
        if item.get("use_for_video"):
            add(item.get("url", ""), "visual_reference", item)
    for item in creative:
        add(item.get("url", ""), "visual_reference", item)
    # Copy/text assets are only references for claims or usage cues, not exact shots.
    for item in copy_assets + text_assets:
        add(item.get("url", ""), "copy_or_usage_reference", item)

    assets = list(by_url.values())
    def score(asset: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
        role_penalty = 0 if asset["role"] == "visual_reference" else 3
        text_penalty = 1 if asset.get("text_density") in {"high", "medium"} else 0
        original_order = int(asset.get("source_order", 9999))
        return (role_penalty + text_penalty, role_penalty, text_penalty, original_order, len(asset["url"]))

    sorted_assets = sorted(assets, key=score)
    selected: List[Dict[str, Any]] = []

    def pick(predicate) -> None:
        if len(selected) >= max_images:
            return
        for asset in sorted_assets:
            if asset in selected:
                continue
            if predicate(asset):
                selected.append(asset)
                return

    pick(lambda a: a["role"] == "visual_reference" and a.get("text_density") not in {"high", "medium"})
    pick(lambda a: a["role"] == "visual_reference")
    pick(lambda a: a["role"] == "copy_or_usage_reference" or a.get("text_density") in {"medium", "high"})
    for asset in sorted_assets:
        if len(selected) >= max_images:
            break
        if asset not in selected:
            selected.append(asset)
    return selected[:max_images]



def build_generation_prompt(row: Dict[str, str], brief: Dict[str, Any]) -> str:
    """Prompt for an LLM caption generator, not for Ark directly."""
    lang = normalize_language(brief.get("language", "en"))
    language_name = LANGUAGE_NAMES.get(lang, "English")
    return """You are generating an Ark image-to-video caption for a TikTok ad from structured landing-page data.

Task:
Create one final Ark caption/prompt. Do not hard-code product-specific copy that is not supported by the structured input. Use the images only as visual references, never as required first frames, last frames, freeze frames, or exact shots.

Requirements:
1. Output must be a polished 15-second TikTok Non-Spark style UGC ad caption.
2. Every scene must have visual action, on-screen caption, and voiceover that are semantically related.
3. Choose a creator persona and voice persona from the product category and audience context.
4. The landing page dominant language is LANGUAGE_NAME_PLACEHOLDER. All on-screen captions and voiceover must be in LANGUAGE_NAME_PLACEHOLDER. Do not default to English unless LANGUAGE_NAME_PLACEHOLDER is English.
5. Keep captions short, creator-style, and safe-zone aware. Avoid dense text.
6. Use only supported claims from clean_selling_points, image_text_sources, web sources, or description.
7. Avoid medical claims, competitor mentions, invented certifications, fake reviews, and exact recreation of dense text in source images.
8. The final caption must refer to reference images only as visual references for product appearance, usage cues, color/style, and packaging consistency.
9. Write like a director script, not only ad copy. Each timestamp must follow Camera + Subject + Action + Setting + Style/Audio.
10. Use concrete shot language: close-up, medium shot, macro detail, POV, handheld push-in, tracking move, whip-pan, snap zoom, rack focus, jump cut.
11. Shot pacing constraint: in any rolling 3-second window, use at most 4 distinct shots/cuts/camera resets.
12. Include sound design that matches the action: taps, cloth movement, water movement, cap click, whoosh, or room tone when appropriate.
13. Do not use generic filler such as "fast colorful product beauty shots" unless the specific shot, subject, action, and setting are also described.

Structured input JSON:
""" + json.dumps(brief, ensure_ascii=False, indent=2) + """

Return JSON only:
{
  "selected_images": [{"url": "...", "role": "...", "caption_reference_name": "Image 1"}],
  "title_overlays": ["..."],
  "voiceover_lines": ["..."],
  "final_ark_caption": "..."
}
""".replace("LANGUAGE_NAME_PLACEHOLDER", language_name)


def build_final_ark_caption(brief: Dict[str, Any]) -> str:
    product = brief["product"]
    brand = brief["brand"]
    lang = normalize_language(brief.get("language", "en"))
    language_name = LANGUAGE_NAMES.get(lang, "English")
    style = brief["creator_style"]
    scenes = brief["scene_plan"]
    image_lines = []
    for i, asset in enumerate(brief["selected_images"], 1):
        image_lines.append(f"Image {i}: use as {asset['role']} for product appearance, packaging consistency, usage cues, or visual style. Do not copy it as an exact shot.")
    scene_lines = []
    for scene in scenes:
        scene_lines.append(
            f"{scene['time']} {scene.get('label', '')}:\n"
            f"Camera: {scene.get('shot_type', 'handheld close-up')}; {scene.get('camera', '')}\n"
            f"Subject/action/setting: {scene.get('subject', '')}; {scene.get('action', scene['visual'])}; {scene.get('setting', '')}\n"
            f"Lighting/style: {scene.get('lighting', 'natural UGC lighting')}\n"
            f"On-screen caption exactly: \"{scene['on_screen_caption']}\"\n"
            f"Voiceover: \"{scene['voiceover']}\"\n"
            f"Audio/SFX: {scene.get('audio', 'upbeat music under the voiceover')}"
        )
    return f"""Create a 15-second vertical 9:16 TikTok Non-Spark style UGC product ad for {product}.

Use the uploaded images only as visual references for product appearance, packaging, color palette, usage cues, and benefit cues. Do not treat any image as a required first frame, last frame, freeze frame, or exact shot. Keep the {brand or 'brand'} product and packaging consistent with the references.

Platform style:
Make it feel like a native TikTok creator ad for the landing page's target language audience, not a polished TV commercial. The landing page's dominant language is {language_name}; all on-screen captions and voiceover must be in {language_name}. Use {', '.join(style['visual_style'])}, simple creator captions, natural lighting, and close-up product moments.

Director script standard:
For every timestamp, follow Camera + Subject + Action + Setting + Style/Audio. Use concrete shot types and movement, such as handheld close-up, first-person POV, macro detail, snap zoom, rack focus, tracking move, whip-pan, and beat-matched jump cuts. The visual, on-screen caption, voiceover, and SFX must describe the same moment rather than four unrelated ideas.
Shot pacing constraint: in any rolling 3-second window, use at most 4 distinct shots/cuts/camera resets.

Creator persona:
{style['persona']}. If a person appears, use a natural foreign/international creator look suitable for US/EU TikTok ads. Scene world: {style['scene_world']}. No full face is required unless it naturally fits; prioritize hands, real setting, and product close-ups.

Voice persona:
{style['voice']}. Voiceover must be in {language_name}. Use a native or near-native {language_name}-speaking foreign creator voice. Avoid announcer voice or corporate narration.

Reference image usage:
""" + "\n".join(image_lines) + f"""

Timeline. Keep each shot visually matched to the voiceover:

""" + "\n\n".join(scene_lines) + """

Text requirements:
Use short {language_name} creator-style captions only. Keep captions large, simple, high contrast, bold sans-serif, and inside TikTok safe zones. Put captions in upper-middle or center-left. Avoid the bottom 20% and right-side UI area. Do not add extra unrequested words. Do not use Chinese or mixed-language captions.

Audio:
Use upbeat generated music under the voiceover. Voice should be fast but clear, natural, and creator-like.

Compliance and quality:
No medical claims, no competitor products, no invented certifications, no fake review text, no extra logos, no dense text overlays, no unreadable package text recreation. Product label and packaging should remain as consistent as possible with the reference images."""


def build_brief(row: Dict[str, str], max_images: int) -> Dict[str, Any]:
    _ = (row, max_images)
    raise RuntimeError(
        "Local caption brief generation has been removed. "
        "Use build_brief_input(...) plus the ModelHub caption-brief generator in url_to_ark_video.py."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--case-id", default="525055")
    parser.add_argument("--row-index", type=int, default=0)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-images", type=int, default=3)
    args = parser.parse_args()

    row = load_row(Path(args.input), args.case_id, args.row_index)
    brief = build_brief_input(row, args.max_images)
    generation_prompt = json.dumps({
        "message": "This is raw LLM input only. Run url_to_ark_video.py to generate the LLM caption brief and Ark caption.",
        "brief_input": brief,
    }, ensure_ascii=False, indent=2)
    final_caption = ""

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_id = brief.get("case_id") or "row"
    (out_dir / f"{case_id}_caption_brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{case_id}_caption_generation_prompt.txt").write_text(generation_prompt, encoding="utf-8")
    (out_dir / f"{case_id}_ark_caption.txt").write_text(final_caption, encoding="utf-8")
    (out_dir / f"{case_id}_ark_payload_content.json").write_text(json.dumps({
        "content": [{"type": "text", "text": final_caption}] + [
            {"type": "image_url", "image_url": {"url": asset["url"]}, "role": "reference_image"}
            for asset in brief["selected_images"]
        ]
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"brief: {out_dir / f'{case_id}_caption_brief.json'}")
    print(f"caption_generation_prompt: {out_dir / f'{case_id}_caption_generation_prompt.txt'}")
    print(f"ark_caption: {out_dir / f'{case_id}_ark_caption.txt'}")
    print(f"selected_images: {len(brief['selected_images'])}")
    for i, asset in enumerate(brief["selected_images"], 1):
        print(f"  Image {i}: {asset['url']} ({asset['role']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
