#!/usr/bin/env python3
"""Build an offline TikTok campaign upload/create manifest from media deliverables.

This script does not call TikTok APIs. It validates local/public media and
proposes ad group assignments that always include at least one video per ad
group. By default it includes generated assets plus URL-derived source assets
that are already normalized for campaign use:

- generated_videos / generated_images
- selected_original_9x16_assets or media_deliverables/source_selected_images_9x16
- landing_page_videos_9x16 or media_deliverables/source_selected_videos_9x16
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def norm_text(value: Any) -> str:
    return str(value or "").strip()


def natural_key(path: Path) -> Tuple[Any, ...]:
    parts = re.split(r"(\d+)", str(path))
    return tuple(int(p) if p.isdigit() else p for p in parts)


def first_public_url(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        if isinstance(value, list):
            nested = first_public_url(*value)
            if nested:
                return nested
        if isinstance(value, dict):
            nested = first_public_url(value.get("public_url"), value.get("url"), value.get("cdn_url"))
            if nested:
                return nested
    return ""


def resolve_path(raw: Any, base: Path) -> Optional[Path]:
    text = norm_text(raw)
    if not text:
        return None
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def media_record(
    kind: str,
    index: int,
    path: Path,
    public_url: str = "",
    plan: Optional[Dict[str, Any]] = None,
    source: str = "",
    asset_origin: str = "generated",
    campaign_role: str = "",
    source_url: str = "",
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "asset_type": kind,
        "index": index,
        "local_path": str(path),
        "public_url": public_url,
        "file_name": path.name,
        "plan": plan or {},
        "source": source,
        "asset_origin": asset_origin,
        "campaign_role": campaign_role or ("anchor_video" if kind == "video" else "image_variant"),
        "source_url": source_url,
        "exists": path.exists(),
    }


def media_sort_key(item: Dict[str, Any]) -> Tuple[Any, ...]:
    origin_rank = {
        "generated": 0,
        "landing_page_video_9x16": 1,
        "source_selected_9x16": 1,
        "source_selected": 2,
        "legacy": 9,
    }.get(norm_text(item.get("asset_origin")), 5)
    plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
    plan_index = plan.get("index")
    if isinstance(plan_index, int):
        return (origin_rank, 0, plan_index, natural_key(Path(item.get("local_path", ""))))
    return (origin_rank, 1, natural_key(Path(item.get("local_path", ""))))


def add_unique(target: List[Dict[str, Any]], seen: set[str], item: Dict[str, Any]) -> None:
    key = str(Path(item.get("local_path", "")).expanduser())
    if key in seen:
        return
    seen.add(key)
    target.append(item)


def collect_manifest_items(
    manifest: Dict[str, Any],
    base: Path,
    include_source_assets: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    videos: List[Dict[str, Any]] = []
    images: List[Dict[str, Any]] = []
    seen_videos: set[str] = set()
    seen_images: set[str] = set()

    for idx, item in enumerate(manifest.get("generated_videos", []) or [], 1):
        p = resolve_path(item.get("deliverable_path") or item.get("source_path") or item.get("path"), base)
        if not p:
            continue
        add_unique(videos, seen_videos, media_record(
            "video",
            idx,
            p,
            first_public_url(item.get("public_video_url"), item.get("public_upload"), item.get("public_url"), item.get("video_url")),
            item.get("plan") or {},
            "media_deliverables_manifest.generated_videos",
            "generated",
            "anchor_video",
            norm_text(item.get("video_url")),
        ))

    for idx, item in enumerate(manifest.get("generated_images", []) or [], 1):
        p = resolve_path(item.get("deliverable_path") or item.get("source_path") or item.get("path"), base)
        if not p:
            continue
        add_unique(images, seen_images, media_record(
            "image",
            idx,
            p,
            first_public_url(item.get("public_url"), item.get("public_upload"), item.get("public_urls")),
            item.get("plan") or {},
            "media_deliverables_manifest.generated_images",
            "generated",
            "image_variant",
            norm_text(item.get("url")),
        ))

    if include_source_assets:
        for idx, item in enumerate(manifest.get("source_selected_images_9x16", []) or [], 1):
            p = resolve_path(item.get("deliverable_path") or item.get("source_path") or item.get("path"), base)
            if not p:
                continue
            add_unique(images, seen_images, media_record(
                "image",
                idx,
                p,
                first_public_url(item.get("public_url"), item.get("public_upload")),
                {},
                "media_deliverables_manifest.source_selected_images_9x16",
                "source_selected_9x16",
                "source_image_variant",
                norm_text(item.get("url") or item.get("source_url")),
            ))

        for idx, item in enumerate(manifest.get("source_selected_videos_9x16", []) or [], 1):
            p = resolve_path(item.get("deliverable_path") or item.get("source_path") or item.get("path"), base)
            if not p:
                continue
            add_unique(videos, seen_videos, media_record(
                "video",
                idx,
                p,
                first_public_url(item.get("public_url"), item.get("public_upload")),
                {},
                "media_deliverables_manifest.source_selected_videos_9x16",
                "landing_page_video_9x16",
                "source_video_anchor",
                norm_text(item.get("url") or item.get("source_url")),
            ))

    return videos, images


def collect_from_deliverables_manifest(
    manifest_path: Path,
    include_source_assets: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    manifest = load_json(manifest_path, {})
    base = manifest_path.parent
    videos, images = collect_manifest_items(manifest, base, include_source_assets)
    videos = sorted(videos, key=media_sort_key)
    images = sorted(images, key=media_sort_key)
    for idx, item in enumerate(videos, 1):
        item["index"] = idx
    for idx, item in enumerate(images, 1):
        item["index"] = idx
    return videos, images, manifest


def glob_media(directory: Path, extensions: Iterable[str]) -> List[Path]:
    if not directory.exists():
        return []
    exts = {x.lower() for x in extensions}
    return sorted([p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in exts], key=natural_key)


def collect_from_run_dir(
    run_dir: Path,
    include_source_assets: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    deliverables = run_dir / "media_deliverables" / "manifest.json"
    if deliverables.exists():
        return collect_from_deliverables_manifest(deliverables, include_source_assets)

    result_files = sorted(run_dir.glob("*_generation_results.json"), key=natural_key)
    results = load_json(result_files[-1], {}) if result_files else {}
    videos: List[Dict[str, Any]] = []
    images: List[Dict[str, Any]] = []
    seen_videos: set[str] = set()
    seen_images: set[str] = set()

    for idx, item in enumerate(results.get("videos", []) or [], 1):
        p = resolve_path(item.get("video_path"), run_dir)
        if p:
            add_unique(videos, seen_videos, media_record(
                "video",
                idx,
                p,
                first_public_url(item.get("public_video_url"), item.get("public_upload"), item.get("video_url")),
                item.get("plan") or {},
                "generation_results.videos",
                "generated",
                "anchor_video",
                norm_text(item.get("video_url")),
            ))
    for idx, p in enumerate(glob_media(run_dir / "generated_videos", VIDEO_EXTS), len(videos) + 1):
        add_unique(videos, seen_videos, media_record("video", idx, p, "", {}, "legacy.generated_videos", "generated", "anchor_video"))

    img_idx = 0
    for item in results.get("image_assets", []) or []:
        plan = item.get("plan") or {}
        public_urls = item.get("public_urls") or []
        public_uploads = item.get("public_uploads") or []
        for saved_path_index, saved_path in enumerate(item.get("saved_paths", []) or []):
            img_idx += 1
            p = resolve_path(saved_path, run_dir)
            if not p:
                continue
            add_unique(images, seen_images, media_record(
                "image",
                img_idx,
                p,
                first_public_url(
                    public_uploads[saved_path_index] if saved_path_index < len(public_uploads) else "",
                    public_urls[saved_path_index] if saved_path_index < len(public_urls) else "",
                    public_urls,
                ),
                plan,
                "generation_results.image_assets",
                "generated",
                "image_variant",
            ))
    for idx, p in enumerate(glob_media(run_dir / "generated_image_assets", IMAGE_EXTS), len(images) + 1):
        add_unique(images, seen_images, media_record("image", idx, p, "", {}, "legacy.generated_image_assets", "generated", "image_variant"))

    if include_source_assets:
        for idx, p in enumerate(glob_media(run_dir / "selected_original_9x16_assets", IMAGE_EXTS), len(images) + 1):
            add_unique(images, seen_images, media_record("image", idx, p, "", {}, "legacy.selected_original_9x16_assets", "source_selected_9x16", "source_image_variant"))
        for idx, p in enumerate(glob_media(run_dir / "media_deliverables" / "source_selected_images_9x16", IMAGE_EXTS), len(images) + 1):
            add_unique(images, seen_images, media_record("image", idx, p, "", {}, "legacy.media_deliverables.source_selected_images_9x16", "source_selected_9x16", "source_image_variant"))
        for idx, p in enumerate(glob_media(run_dir / "landing_page_videos_9x16", VIDEO_EXTS), len(videos) + 1):
            add_unique(videos, seen_videos, media_record("video", idx, p, "", {}, "legacy.landing_page_videos_9x16", "landing_page_video_9x16", "source_video_anchor"))
        for idx, p in enumerate(glob_media(run_dir / "media_deliverables" / "source_selected_videos_9x16", VIDEO_EXTS), len(videos) + 1):
            add_unique(videos, seen_videos, media_record("video", idx, p, "", {}, "legacy.media_deliverables.source_selected_videos_9x16", "landing_page_video_9x16", "source_video_anchor"))

    videos = sorted(videos, key=media_sort_key)
    images = sorted(images, key=media_sort_key)
    for idx, item in enumerate(videos, 1):
        item["index"] = idx
    for idx, item in enumerate(images, 1):
        item["index"] = idx
    return videos, images, {"run_dir": str(run_dir), "generation_results": str(result_files[-1]) if result_files else ""}


def choose_group_count(video_count: int, image_count: int, min_images_per_group: int, max_groups: int) -> int:
    # Videos are the only hard requirement for an ad group; images are a soft
    # target. As long as there is at least one video we form one group per video
    # (capped by max_groups), even with zero images. min_images_per_group only
    # controls how many image slots build_groups tries to fill per group, and a
    # shortfall is surfaced as a per-group warning, not a blocker.
    if video_count <= 0:
        return 0
    return max(1, min(video_count, max_groups))


def ordered_images_by_origin(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Interleave images across origins (generated first) for balanced spread."""
    image_order: List[Dict[str, Any]] = []
    origin_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for image in images:
        origin = norm_text(image.get("asset_origin")) or "unknown"
        origin_buckets.setdefault(origin, []).append(image)
    origin_names = sorted(origin_buckets.keys(), key=lambda k: (0 if k == "generated" else 1, k))
    while any(origin_buckets.values()):
        for origin in origin_names:
            if origin_buckets.get(origin):
                image_order.append(origin_buckets[origin].pop(0))
    return image_order


def build_carousel_image_group(images: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    """One dedicated ad group holding all images as a single carousel/gallery ad."""
    ordered = ordered_images_by_origin(images)
    return {
        "adgroup_index": index,
        "adgroup_name": f"adgroup_{index:02d}_carousel_images",
        "angle_hint": "carousel_image_gallery",
        "videos": [],
        "images": ordered,
        "extra_videos": [],
        "is_carousel": True,
        "ad_candidates": [{
            "ad_index": 1,
            "creative_type": "carousel",
            "assets": ordered,
            "image_count": len(ordered),
            "operation_status": "DISABLE",
            "placement_note": "Carousel/gallery ad bundles multiple image_ids into one ad; verify the ad group placement supports carousel (TikTok carousel / Pangle / GAB) and supply a music_id if required.",
        }],
    }


def build_groups(videos: List[Dict[str, Any]], images: List[Dict[str, Any]], min_images_per_group: int, max_groups: int, image_ad_mode: str = "distribute") -> List[Dict[str, Any]]:
    group_count = choose_group_count(len(videos), len(images), min_images_per_group, max_groups)
    groups: List[Dict[str, Any]] = []

    # Carousel mode: video groups carry videos only; all images go to one dedicated
    # carousel ad group. This avoids SINGLE_IMAGE ads on TikTok-only placements and
    # matches the "3 videos -> 3 groups + 6 images -> 1 gallery group" layout.
    if image_ad_mode == "carousel":
        for i in range(group_count):
            groups.append({
                "adgroup_index": i + 1,
                "adgroup_name": f"adgroup_{i + 1:02d}",
                "angle_hint": f"creative_test_angle_{i + 1:02d}",
                "videos": [videos[i]],
                "images": [],
                "extra_videos": [],
                "ad_candidates": [],
            })
        for idx, video in enumerate(videos[group_count:]):
            groups[idx % len(groups)]["extra_videos"].append(video)
        for group in groups:
            ad_index = 1
            for video in group.get("videos", []) + group.get("extra_videos", []):
                group["ad_candidates"].append({"ad_index": ad_index, "creative_type": "video", "asset": video, "operation_status": "DISABLE"})
                ad_index += 1
        if images:
            groups.append(build_carousel_image_group(images, len(groups) + 1))
        return groups

    if group_count <= 0:
        return groups

    for i in range(group_count):
        groups.append({
            "adgroup_index": i + 1,
            "adgroup_name": f"adgroup_{i + 1:02d}",
            "angle_hint": f"creative_test_angle_{i + 1:02d}",
            "videos": [videos[i]],
            "images": [],
            "extra_videos": [],
            "ad_candidates": [],
        })

    # Spread generated/source images across groups instead of clustering by origin.
    image_order = ordered_images_by_origin(images)

    image_cursor = 0
    for slot in range(min_images_per_group):
        rotated_groups = groups[slot % len(groups):] + groups[:slot % len(groups)]
        for group in rotated_groups:
            if image_cursor < len(image_order):
                group["images"].append(image_order[image_cursor])
                image_cursor += 1

    group_cursor = 0
    while image_cursor < len(image_order):
        groups[group_cursor % len(groups)]["images"].append(image_order[image_cursor])
        image_cursor += 1
        group_cursor += 1

    for idx, video in enumerate(videos[group_count:]):
        groups[idx % len(groups)]["extra_videos"].append(video)

    for group in groups:
        ad_index = 1
        for video in group.get("videos", []) + group.get("extra_videos", []):
            group["ad_candidates"].append({
                "ad_index": ad_index,
                "creative_type": "video",
                "asset": video,
                "operation_status": "DISABLE",
            })
            ad_index += 1
        for image in group.get("images", []):
            group["ad_candidates"].append({
                "ad_index": ad_index,
                "creative_type": "image",
                "asset": image,
                "operation_status": "DISABLE",
                "placement_note": "Single-image ads need an image-capable placement/format; TikTok-only feed placement may reject SINGLE_IMAGE.",
            })
            ad_index += 1
    return groups


def count_by_origin(items: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        origin = norm_text(item.get("asset_origin")) or "unknown"
        out[origin] = out.get(origin, 0) + 1
    return out


def validate(groups: List[Dict[str, Any]], videos: List[Dict[str, Any]], images: List[Dict[str, Any]], min_images_per_group: int) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    has_carousel = any(g.get("is_carousel") for g in groups)
    if not videos and not has_carousel:
        issues.append({"level": "blocker", "message": "No videos found; every video ad group requires at least one video."})
    if len(images) < min_images_per_group and not has_carousel:
        issues.append({"level": "warning", "message": f"Only {len(images)} image(s) available (<{min_images_per_group} per group target); ad groups will be created video-only or with fewer images. Add more images for the default image-variant coverage."})
    for group in groups:
        if group.get("is_carousel"):
            if len(group.get("images", [])) < 2:
                issues.append({"level": "warning", "message": f"{group['adgroup_name']} is a carousel group with fewer than 2 images; carousel ads usually need 2+ images."})
            continue
        if not group.get("videos"):
            issues.append({"level": "blocker", "message": f"{group['adgroup_name']} has no anchor video."})
        if len(group.get("images", [])) < min_images_per_group:
            issues.append({"level": "warning", "message": f"{group['adgroup_name']} has fewer than {min_images_per_group} images."})
    missing_urls = [x for x in videos + images if not x.get("public_url")]
    if missing_urls:
        issues.append({"level": "warning", "message": f"{len(missing_urls)} assets do not have public URLs yet; TikTok upload-by-URL needs public URLs or a direct file upload tool."})
    missing_files = [x for x in videos + images if not x.get("exists")]
    if missing_files:
        issues.append({"level": "blocker", "message": f"{len(missing_files)} local asset paths do not exist."})
    issues.append({"level": "note", "message": "Actual TikTok upload/create is not executed by this manifest; require same-turn explicit confirmation before any write API call."})
    issues.append({"level": "note", "message": "All campaign/adgroup/ad create requests must use operation_status=DISABLE."})
    issues.append({"level": "note", "message": "TikTok-only placements may not accept SINGLE_IMAGE ads; image ads need an image-capable placement/format such as Pangle/GAB or carousel."})
    return issues


def build_manifest(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = Path(args.input).expanduser().resolve()
    include_source_assets = not args.generated_only
    if input_path.is_file():
        videos, images, source_manifest = collect_from_deliverables_manifest(input_path, include_source_assets)
        run_dir = input_path.parents[1] if input_path.name == "manifest.json" and input_path.parent.name == "media_deliverables" else input_path.parent
    else:
        run_dir = input_path
        videos, images, source_manifest = collect_from_run_dir(run_dir, include_source_assets)
    groups = build_groups(videos, images, args.min_images_per_group, args.max_groups, image_ad_mode=args.image_ad_mode)
    issues = validate(groups, videos, images, args.min_images_per_group)
    return {
        "mode": "offline_dry_run",
        "source": str(input_path),
        "run_dir": str(run_dir),
        "policy": {
            "include_source_assets": include_source_assets,
            "included_source_asset_dirs": ["selected_original_9x16_assets", "landing_page_videos_9x16", "media_deliverables/source_selected_images_9x16", "media_deliverables/source_selected_videos_9x16"] if include_source_assets else [],
            "min_videos_per_adgroup": 1,
            "min_images_per_adgroup": args.min_images_per_group,
            "max_groups": args.max_groups,
            "image_ad_mode": args.image_ad_mode,
            "grouping": (
                "one anchor video per video adgroup; all images grouped into one dedicated carousel/gallery ad group (creative_type=carousel); extra videos attached round-robin as extra video ads"
                if args.image_ad_mode == "carousel"
                else "one anchor video per adgroup first; two images per adgroup by default; distribute extra images round-robin; attach extra videos round-robin as extra video ads"
            ),
            "campaign_create_status": "DISABLE",
            "adgroup_create_status": "DISABLE",
            "ad_create_status": "DISABLE",
        },
        "counts": {
            "videos": len(videos),
            "images": len(images),
            "adgroups": len(groups),
            "video_origins": count_by_origin(videos),
            "image_origins": count_by_origin(images),
            "ad_candidates": sum(len(g.get("ad_candidates", [])) for g in groups),
        },
        "materials": {
            "videos": videos,
            "images": images,
        },
        "groups": groups,
        "unassigned": {
            "videos": [] if groups else videos,
            "images": [] if groups else images,
        },
        "upload_plan": {
            "provider": "TikTok upload-by-URL when public_url exists; otherwise direct upload tool if available",
            "videos": [{"file_name": v["file_name"], "public_url": v.get("public_url", ""), "local_path": v["local_path"], "asset_origin": v.get("asset_origin", "")} for v in videos],
            "images": [{"file_name": img["file_name"], "public_url": img.get("public_url", ""), "local_path": img["local_path"], "asset_origin": img.get("asset_origin", "")} for img in images],
        },
        "campaign_safety": {
            "write_actions_executed": False,
            "operation_status": "DISABLE",
            "requires_user_confirmation_before_upload_or_create": True,
            "confirmation_scope": "upload media to TikTok and create disabled campaign/adgroups/ads from this exact manifest",
        },
        "issues": issues,
        "source_manifest_summary": source_manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build offline campaign upload/create manifest from generated and selected source media")
    parser.add_argument("--input", required=True, help="Run directory or media_deliverables/manifest.json")
    parser.add_argument("--output", default="", help="Output JSON path; defaults to <run_dir>/campaign_upload_manifest.json")
    parser.add_argument("--min-images-per-group", type=int, default=2)
    parser.add_argument("--max-groups", type=int, default=99)
    parser.add_argument("--image-ad-mode", choices=["distribute", "carousel"], default="distribute", help="How images become ads. 'distribute' (default) spreads images across the video ad groups as single-image ad candidates. 'carousel' puts all images into one dedicated ad group as a single carousel/gallery ad (creative_type=carousel), leaving the video ad groups video-only.")
    parser.add_argument("--include-source-assets", action="store_true", default=True, help="Include selected_original_9x16_assets and landing_page_videos_9x16; default on")
    parser.add_argument("--generated-only", action="store_true", help="Use only generated images/videos; exclude selected original 9:16 images and landing-page 9:16 videos")
    args = parser.parse_args()
    manifest = build_manifest(args)
    output = Path(args.output).expanduser().resolve() if args.output else Path(manifest["run_dir"]) / "campaign_upload_manifest.json"
    dump_json(output, manifest)
    print(json.dumps({"output": str(output), "counts": manifest["counts"], "issues": manifest["issues"]}, ensure_ascii=False, indent=2))
    return 0 if not any(i.get("level") == "blocker" for i in manifest["issues"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
