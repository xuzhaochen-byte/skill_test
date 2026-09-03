#!/usr/bin/env python3
"""Enrich the static benchmark report with reference creatives and video analysis.

Run this after CTR Top discovery, similarity filtering, downloads, and optional
MLLM video analysis. It keeps the benchmark payload intact and adds compact
frontend-friendly sections before rewriting report-data.js.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def relpath(path_value: Any, output_dir: Path) -> Optional[str]:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        # Manifest paths are usually project-root relative. Try as-is first for
        # browser use from output_dir, then project-root relative for existence.
        candidate_in_output = output_dir / path
        if candidate_in_output.exists():
            path = candidate_in_output
        else:
            path = Path.cwd() / path
    if not path.exists():
        return None
    try:
        return path.resolve().relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_uri()


def domain_of(url: Any) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else "https://" + text)
    return parsed.netloc.replace("www.", "")


def preview_url(video_id: Any, explicit: Any = None) -> str:
    explicit_text = str(explicit or "").strip()
    if explicit_text.startswith("http"):
        return explicit_text
    vid = str(video_id or "").strip()
    if not vid:
        return ""
    return f"https://ad-creative-studio-platform.tiktok-row.net/preview?vid={vid}"


def merge_rows_by_video_id(*rows_lists: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for rows in rows_lists:
        for row in rows or []:
            vid = str(row.get("Video ID") or row.get("video_id") or "").strip()
            if not vid:
                continue
            merged.setdefault(vid, {}).update(row)
    return merged


def build_reference_creatives(output_dir: Path) -> Dict[str, Any]:
    top = load_json(output_dir / "ctr_top50_videos.json", {})
    sim = load_json(output_dir / "similar_landing_pages.json", {})
    manifest = load_json(output_dir / "download_manifest.json", {})

    top_rows = top.get("rows") if isinstance(top, dict) else []
    sim_rows = sim.get("rows") if isinstance(sim, dict) else []
    dl_rows = manifest.get("videos") if isinstance(manifest, dict) else []
    if not isinstance(top_rows, list):
        top_rows = []
    if not isinstance(sim_rows, list):
        sim_rows = []
    if not isinstance(dl_rows, list):
        dl_rows = []

    by_vid = merge_rows_by_video_id(top_rows, sim_rows, dl_rows)
    selected_vids: List[str] = []
    # Prefer similarity-filtered rows for relevance, then append remaining top rows.
    for rows in (sim_rows, top_rows):
        for row in rows:
            vid = str(row.get("Video ID") or row.get("video_id") or "").strip()
            if vid and vid not in selected_vids:
                selected_vids.append(vid)

    items = []
    for vid in selected_vids[:50]:
        row = by_vid.get(vid, {})
        ctr = parse_float(row.get("CTR") if row.get("CTR") is not None else row.get("ctr"))
        external_url = row.get("External Website URL") or row.get("external_url") or ""
        local_video_path = relpath(row.get("path"), output_dir) if row.get("status") == "ok" else None
        item = {
            "rank": row.get("rank"),
            "video_id": vid,
            "video_url": preview_url(vid, row.get("Video URL") or row.get("preview_url")),
            "local_video_path": local_video_path,
            "external_url": external_url,
            "domain": row.get("External URL Domains") or domain_of(external_url),
            "advertiser_name": row.get("Advertiser Name") or "",
            "brand_name": row.get("Brand Name (Latest)") or "",
            "account_industry_l3": row.get("Account Industry Level 3 Name V40 (Latest)") or "",
            "ctr": ctr,
            "formatted_ctr": f"{ctr * 100:.2f}%" if ctr is not None else "--",
            "impressions": parse_float(row.get("Impressions")),
            "clicks": parse_float(row.get("Clicks (Destination)")),
            "similarity_score": parse_float(row.get("similarity_score")),
            "similarity_reason": row.get("reason") or "",
            "transferable_points": row.get("transferable_points") or [],
            "download_status": row.get("status") or "not_attempted",
        }
        if item.get("local_video_path"):
            items.append(item)

    # The report should present the strongest reference creatives first. The
    # similarity output may be sorted by similarity or acceptance status, so
    # normalize the final webpage order by CTR descending with rank as a stable
    # tie-breaker.
    items.sort(key=lambda x: (-(x.get("ctr") if x.get("ctr") is not None else -1.0), int(parse_float(x.get("rank")) or 999999)))

    return {
        "top_count": len(top_rows),
        "accepted_count": int(sim.get("accepted_count", len(sim_rows))) if isinstance(sim, dict) else len(sim_rows),
        "downloaded_count": len([x for x in dl_rows if x.get("status") == "ok"]),
        "displayed_count": len(items),
        "threshold": sim.get("threshold") if isinstance(sim, dict) else None,
        "filters": top.get("filters", {}) if isinstance(top, dict) else {},
        "items": items,
    }


def build_video_analysis_summary(output_dir: Path) -> Dict[str, Any]:
    data = load_json(output_dir / "video_analysis.json", {})
    if not isinstance(data, dict):
        return {"videos_analyzed": 0, "items": [], "recommendations": {}}
    items = []
    for analysis in data.get("video_analyses") or []:
        if not isinstance(analysis, dict):
            continue
        src = analysis.get("source") or {}
        first = analysis.get("first_3_seconds") or {}
        items.append({
            "video_id": analysis.get("video_id") or src.get("video_id"),
            "rank": src.get("rank"),
            "ctr": parse_float(src.get("ctr")),
            "formatted_ctr": f"{parse_float(src.get('ctr')) * 100:.2f}%" if parse_float(src.get("ctr")) is not None else "--",
            "external_url": src.get("external_url") or "",
            "preview_url": src.get("preview_url") or preview_url(src.get("video_id") or analysis.get("video_id")),
            "local_video_path": relpath(src.get("path"), output_dir),
            "first_3_seconds": first,
            "creative_structure": analysis.get("creative_structure") or [],
            "selling_points": analysis.get("selling_points") or [],
            "visual_patterns": analysis.get("visual_patterns") or [],
            "copy_patterns": analysis.get("copy_patterns") or [],
            "transferable_to_customer": analysis.get("transferable_to_customer") or [],
            "not_recommended_to_copy": analysis.get("not_recommended_to_copy") or [],
            "confidence": parse_float(analysis.get("confidence")),
            "error": analysis.get("error"),
        })
    return {
        "videos_analyzed": len(items),
        "items": items,
        "recommendations": data.get("recommendations") or {},
    }


def copy_template(output_dir: Path, template_dir: Path) -> None:
    if not template_dir.exists():
        return
    for item in template_dir.iterdir():
        if item.name == "report-data.template.js":
            continue
        target = output_dir / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich benchmark HTML report with reference creatives and MLLM recommendations.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--benchmark-result", type=Path, default=None)
    parser.add_argument("--template-dir", type=Path, default=None)
    parser.add_argument("--no-template-copy", action="store_true", help="Only rewrite report-data.js; do not refresh HTML/CSS/JS template files")
    args = parser.parse_args()

    output_dir = args.output_dir
    benchmark_path = args.benchmark_result or output_dir / "benchmark_result.json"
    result = load_json(benchmark_path, {})
    if not isinstance(result, dict) or not result:
        raise RuntimeError(f"benchmark result not found or invalid: {benchmark_path}")

    result["reference_creatives"] = build_reference_creatives(output_dir)
    result["video_analysis_summary"] = build_video_analysis_summary(output_dir)

    skill_root = Path(__file__).resolve().parents[1]
    template_dir = args.template_dir or skill_root / "assets" / "benchmark-report-template"
    if not args.no_template_copy:
        copy_template(output_dir, template_dir)

    (output_dir / "benchmark_result_enriched.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report-data.js").write_text("window.BENCHMARK_REPORT_DATA = " + json.dumps(result, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    if not (output_dir / "index.html").exists():
        (output_dir / "index.html").write_text("<!doctype html><meta charset='utf-8'><title>Benchmark Report</title><script src='report-data.js'></script><script src='app.js'></script>", encoding="utf-8")

    print(json.dumps({
        "report": str(output_dir / "index.html"),
        "data": str(output_dir / "benchmark_result_enriched.json"),
        "reference_creatives": len(result["reference_creatives"].get("items", [])),
        "videos_analyzed": result["video_analysis_summary"].get("videos_analyzed", 0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
