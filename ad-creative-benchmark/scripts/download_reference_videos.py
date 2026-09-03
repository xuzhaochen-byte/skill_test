#!/usr/bin/env python3
"""Download selected reference videos from a similar_landing_pages.json result."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def load_rows(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"No rows found in {path}")
    return [dict(r) for r in rows]


def selected_rows(rows: List[Dict[str, Any]], threshold: float, include_uncertain: bool) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        vid = str(row.get("Video ID") or row.get("video_id") or "").strip()
        if not vid:
            continue
        score = parse_float(row.get("similarity_score")) or 0.0
        if (row.get("is_similar") and score >= threshold) or (include_uncertain and score >= threshold):
            out.append(row)
    out.sort(key=lambda r: (-(parse_float(r.get("similarity_score")) or 0.0), int(parse_float(r.get("rank")) or 999999)))
    return out


def download_one(*, row: Dict[str, Any], index: int, total: int, downloader: Path, video_dir: Path, resolution: str, overwrite: bool, retries: int, retry_backoff: float) -> Dict[str, Any]:
    vid = str(row.get("Video ID") or row.get("video_id") or "").strip()
    output = video_dir / f"{vid}.mp4"
    cmd = [sys.executable, str(downloader), str(vid), "-o", str(output), "-r", resolution]
    if overwrite:
        cmd.append("--overwrite")
    attempts = max(1, retries)
    last_proc: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(1, attempts + 1):
        print(f"[{index}/{total}] downloading {vid} attempt {attempt}/{attempts}", file=sys.stderr)
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        last_proc = proc
        if proc.returncode == 0 and output.exists() and output.stat().st_size > 0:
            break
        if output.exists() and (overwrite or output.stat().st_size == 0 or proc.returncode != 0):
            try:
                os.remove(output)
            except OSError:
                pass
        if attempt < attempts:
            time.sleep(retry_backoff * (2 ** (attempt - 1)))
    assert last_proc is not None
    proc = last_proc
    ok = proc.returncode == 0 and output.exists() and output.stat().st_size > 0
    entry = {
        "video_id": vid,
        "rank": row.get("rank"),
        "ctr": row.get("CTR"),
        "external_url": row.get("External Website URL"),
        "preview_url": row.get("Video URL") or f"https://ad-creative-studio-platform.tiktok-row.net/preview?vid={vid}",
        "similarity_score": row.get("similarity_score"),
        "path": str(output),
        "status": "ok" if ok else "failed",
        "attempts": attempts,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }
    if not ok:
        print(f"[{index}/{total}] failed {vid}: {proc.stderr.strip()[:500]}", file=sys.stderr)
    else:
        print(f"[{index}/{total}] done {vid}", file=sys.stderr)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Download reference videos selected by landing-page similarity.")
    parser.add_argument("--similar-pages", type=Path, required=True, help="similar_landing_pages.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--max-videos", type=int, default=10)
    parser.add_argument("--resolution", default="best")
    parser.add_argument("--include-uncertain", action="store_true", help="Allow high-score rows even if is_similar=false")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--workers", type=int, default=5, help="Parallel download workers. Default: 5")
    parser.add_argument("--retries", type=int, default=3, help="Download attempts per video. Default: 3")
    parser.add_argument("--retry-backoff", type=float, default=2.0, help="Initial retry backoff seconds. Default: 2")
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parents[1]
    downloader = skill_root / "preview_vid_downloader" / "download_by_vid.py"
    if not downloader.exists():
        raise FileNotFoundError(f"Downloader not found: {downloader}")

    video_dir = args.output_dir / "reference_videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    rows = selected_rows(load_rows(args.similar_pages), args.threshold, args.include_uncertain)[: args.max_videos]
    manifest = {"source": str(args.similar_pages), "threshold": args.threshold, "max_videos": args.max_videos, "videos": []}
    workers = max(1, int(args.workers or 1))
    if workers == 1:
        entries = [
            download_one(
                row=row,
                index=i,
                total=len(rows),
                downloader=downloader,
                video_dir=video_dir,
                resolution=args.resolution,
                overwrite=args.overwrite,
                retries=args.retries,
                retry_backoff=args.retry_backoff,
            )
            for i, row in enumerate(rows, 1)
        ]
    else:
        entries_by_index: Dict[int, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    download_one,
                    row=row,
                    index=i,
                    total=len(rows),
                    downloader=downloader,
                    video_dir=video_dir,
                    resolution=args.resolution,
                    overwrite=args.overwrite,
                    retries=args.retries,
                    retry_backoff=args.retry_backoff,
                ): i
                for i, row in enumerate(rows, 1)
            }
            for future in as_completed(futures):
                index = futures[future]
                entries_by_index[index] = future.result()
        entries = [entries_by_index[i] for i in sorted(entries_by_index)]
    manifest["videos"].extend(entries)
    manifest_path = args.output_dir / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for v in manifest["videos"] if v.get("status") == "ok")
    print(json.dumps({"manifest": str(manifest_path), "downloaded": ok, "attempted": len(manifest["videos"])}, ensure_ascii=False, indent=2))
    return 0 if ok or not rows else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
