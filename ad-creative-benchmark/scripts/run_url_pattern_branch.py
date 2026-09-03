#!/usr/bin/env python3
"""Run URL-driven benchmark/reference-pattern branch.

This wrapper is intended for ``url_to_ark_video.py --parallel-benchmark-command``.
It makes the pattern branch intent-driven for URL generation:

1. If --adv-id is supplied, run the full advertiser benchmark path.
2. Otherwise try URL/domain -> Aeolus adv context.
3. If an adv context is found, run full benchmark metrics/report + Top videos.
4. If not found, classify the URL into an Aeolus industry candidate and run Top
   video pattern discovery only; advertiser benchmark metrics are skipped.
5. Filter similar landing pages, download videos, and analyze them into
   video_analysis.json for generation planning.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent



def has_any_env(names: List[str]) -> bool:
    return any(str(os.environ.get(name, "") or "").strip() for name in names)


def preflight_or_raise(args: argparse.Namespace) -> None:
    """Fail before long Aeolus/download/MLLM work if required auth is absent."""
    missing: List[str] = []
    if shutil.which("bytedcli") is None:
        missing.append("bytedcli command plus Aeolus authentication")
    downloader = ROOT / "preview_vid_downloader" / "download_by_vid.py"
    if not downloader.exists():
        missing.append(f"reference video downloader at {downloader}")
    needs_aidp = (
        args.similarity_provider == "aidp"
        or args.analysis_provider == "aidp"
        or not args.adv_id.strip()  # URL->industry fallback classification uses AIDP if URL->adv_id misses.
    )
    if needs_aidp and not has_any_env(["AIDP_AK_LIST", "AIDP_API_KEYS", "AIDP_AK", "AIDP_API_KEY"]):
        missing.append("AIDP_AK_LIST/AIDP_API_KEYS or AIDP_AK/AIDP_API_KEY for URL industry classification, similarity filtering, and whole-video pattern analysis")
    if args.similarity_provider == "openai" or args.analysis_provider == "openai":
        if not has_any_env(["ARK_API_KEY", "ARK_API_KEYS"]):
            missing.append("ARK_API_KEY or ARK_API_KEYS for OpenAI-compatible similarity/video analysis")
        if args.analysis_provider == "openai" and shutil.which("ffmpeg") is None:
            missing.append("ffmpeg for OpenAI-compatible frame sampling")
    if missing:
        lines = [
            "Preflight failed before starting URL pattern branch. Configure/authenticate these first:",
            *[f"- {item}" for item in missing],
            "Do not paste raw secret values into reports/logs; export them in the shell environment.",
        ]
        raise RuntimeError("\n".join(lines))

def run(cmd: List[str], *, cwd: Path, env: Dict[str, str], allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    print("[run] " + " ".join(shlex.quote(x) for x in cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stdout:
        print(proc.stdout, end="", flush=True)
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr, flush=True)
    if proc.returncode != 0 and not allow_fail:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_from_context(path: Path) -> Dict[str, Any]:
    data = load_json(path)
    return data.get("selected") or {}


def adv_id_from_context(path: Path) -> str:
    data = load_json(path)
    return str(data.get("adv_id") or (data.get("selected") or {}).get("Advertiser ID") or "").strip()


def make_minimal_benchmark_result(*, path: Path, url: str, country: str, primary: str, secondary: str, industry_classification: Dict[str, Any]) -> None:
    payload = {
        "input": {"adv_id": None, "url": url, "country": country},
        "industry_classification": {
            "industry": f"{primary}-{secondary}",
            "method": "url_industry_fallback_no_adv_id",
            "reason": industry_classification.get("reason", "URL could not be resolved to an advertiser; using classified Aeolus industry for reference patterns only."),
        },
        "benchmark": {"country": country, "industry": f"{primary}-{secondary}", "status": "skipped_no_adv_id"},
        "landing_page": {"url": url, "text_excerpt": ""},
        "adv_context": None,
        "url_pattern_branch": {
            "mode": "industry_fallback",
            "note": "Advertiser benchmark metrics were skipped because URL->adv_id resolution failed. CTR Top reference patterns are still generated from the classified industry.",
            "industry_classification": industry_classification,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_benchmark_result_from_context(*, path: Path, ctx_path: Path, adv_id: str, url: str, country: str, primary: str, secondary: str, reason: str) -> None:
    """Synthesize a benchmark_result.json from a resolved adv_context.

    Used when the advertiser was resolved but the percentile/report stage failed.
    CTR Top discovery only needs country + primary/secondary industry (and the
    adv_context source for optional strict matching), so reference patterns can
    still be produced without the full benchmark waterline.
    """
    payload = {
        "input": {"adv_id": adv_id or None, "url": url, "country": country},
        "industry_classification": {"industry": f"{primary}-{secondary}", "method": "adv_context_without_full_benchmark"},
        "benchmark": {"country": country, "industry": f"{primary}-{secondary}", "status": "skipped_benchmark_metrics_failed"},
        "landing_page": {"url": url, "text_excerpt": ""},
        "adv_context": {
            "selected_primary_industry": primary,
            "selected_secondary_industry": secondary,
            "selected_country": country,
            "source": str(ctx_path),
        },
        "url_pattern_branch": {
            "mode": "adv_context_without_full_benchmark",
            "note": reason,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_full_benchmark(args: argparse.Namespace, out_dir: Path, env: Dict[str, str]) -> Optional[Path]:
    adv_id = args.adv_id.strip()
    if not adv_id:
        url_ctx = out_dir / "adv_context.json"
        proc = run([
            sys.executable, str(SCRIPT_DIR / "aeolus_url_adv_context.py"),
            "--url", args.url,
            "--output-dir", str(out_dir),
            "--last-sync-days", str(args.context_last_sync_days),
            "--limit", str(args.context_limit),
            "--min-clicks", str(args.context_min_clicks),
            "--min-impressions", str(args.context_min_impressions),
        ] + (["--country", args.country] if args.country else []), cwd=ROOT, env=env, allow_fail=True)
        if proc.returncode != 0 or not url_ctx.exists():
            print("[url-pattern] URL could not be resolved to adv_id; falling back to industry-only pattern branch.", flush=True)
            return None
        adv_id = adv_id_from_context(url_ctx)
        if not adv_id:
            print("[url-pattern] URL context did not include Advertiser ID; falling back to industry-only pattern branch.", flush=True)
            return None
    else:
        # Symmetric with the URL path: a failed adv-context resolution degrades to
        # the industry fallback instead of hard-crashing the whole pattern branch
        # (and the parallel benchmark command that drives it).
        proc = run([
            sys.executable, str(SCRIPT_DIR / "aeolus_adv_context.py"),
            "--adv-id", adv_id,
            "--output-dir", str(out_dir),
            "--last-sync-days", str(args.context_last_sync_days),
            "--limit", str(args.context_limit),
            "--min-clicks", str(args.context_min_clicks),
            "--min-impressions", str(args.context_min_impressions),
        ], cwd=ROOT, env=env, allow_fail=True)
        if proc.returncode != 0 or not (out_dir / "adv_context.json").exists():
            print("[url-pattern] adv_id context resolution failed; falling back to industry-only pattern branch.", flush=True)
            return None

    ctx_path = out_dir / "adv_context.json"
    selected = selected_from_context(ctx_path)
    country = args.country or selected.get("Ad Country Code", "")
    primary = selected.get("Primary Industry", "")
    secondary = selected.get("Secondary Industry", "")
    selected_url = args.url or selected.get("External Website URL", "")
    if not primary or not secondary:
        print("[url-pattern] adv_context has no primary/secondary industry; falling back to industry-only pattern branch.", flush=True)
        return None

    # Percentile/report stages are best-effort. A failure here must not kill the
    # pattern branch: CTR Top reference discovery only needs country + industry,
    # so on failure we synthesize benchmark_result.json from adv_context and
    # continue. This keeps the adv path symmetric with the URL/industry fallback.
    run([sys.executable, str(SCRIPT_DIR / "aeolus_adv_metrics.py"), "--adv-id", adv_id, "--adv-context", str(ctx_path), "--output-dir", str(out_dir)], cwd=ROOT, env=env, allow_fail=True)
    run([sys.executable, str(SCRIPT_DIR / "aeolus_dynamic_benchmark.py"), "--adv-id", adv_id, "--adv-context", str(ctx_path), "--output-dir", str(out_dir), "--strict-match-level", args.strict_match_level, "--last-sync-days", str(args.benchmark_last_sync_days)], cwd=ROOT, env=env, allow_fail=True)
    report_proc = run([
        sys.executable, str(SCRIPT_DIR / "benchmark_report.py"),
        "--adv-id", adv_id,
        "--url", selected_url,
        "--country", country,
        "--industry", f"{primary}-{secondary}",
        "--adv-context", str(ctx_path),
        "--adv-data", str(out_dir / "adv_metrics_for_benchmark.csv"),
        "--benchmark", str(out_dir / "dynamic_benchmark_for_report.csv"),
        "--output-dir", str(out_dir),
    ], cwd=ROOT, env=env, allow_fail=True)

    benchmark_result = out_dir / "benchmark_result.json"
    if report_proc.returncode != 0 or not benchmark_result.exists():
        reason = "Advertiser benchmark percentile/report stage failed; using adv_context industry for CTR Top reference patterns only."
        print(f"[url-pattern] {reason}", flush=True)
        make_benchmark_result_from_context(path=benchmark_result, ctx_path=ctx_path, adv_id=adv_id, url=selected_url, country=country, primary=primary, secondary=secondary, reason=reason)
    return benchmark_result


def run_industry_fallback(args: argparse.Namespace, out_dir: Path, env: Dict[str, str]) -> Path:
    run([
        sys.executable, str(SCRIPT_DIR / "aeolus_industry_candidates.py"),
        "--output-dir", str(out_dir),
        "--limit", str(args.industry_candidate_limit),
        "--min-impressions", str(args.industry_min_impressions),
        "--last-sync-days", str(args.industry_last_sync_days),
    ] + (["--country", args.country] if args.country else []), cwd=ROOT, env=env)
    classification_path = out_dir / "url_industry_classification.json"
    classify_cmd = [
        sys.executable, str(SCRIPT_DIR / "classify_url_industry.py"),
        "--url", args.url,
        "--industry-candidates", str(out_dir / "industry_candidates.json"),
        "--output", str(classification_path),
        "--model", args.aidp_model,
        "--endpoint", args.aidp_endpoint,
    ]
    if args.country:
        classify_cmd += ["--country", args.country]
    if args.url_summary:
        classify_cmd += ["--url-summary", args.url_summary]
    run(classify_cmd, cwd=ROOT, env=env)
    cls = load_json(classification_path)
    country = cls.get("country") or args.country
    if not country:
        raise RuntimeError("Industry fallback classification did not produce a country; pass --country.")
    primary = cls["primary_industry"]
    secondary = cls["secondary_industry"]
    benchmark_result = out_dir / "benchmark_result.json"
    make_minimal_benchmark_result(path=benchmark_result, url=args.url, country=country, primary=primary, secondary=secondary, industry_classification=cls)
    return benchmark_result


def write_empty_video_analysis(path: Path, *, url: str, reason: str) -> None:
    """Write a schema-compatible video_analysis.json with zero reference videos.

    Downstream url_to_ark_video.py reads only ``video_analyses`` (an empty list
    yields zero benchmark_reference_patterns), so generation continues cleanly
    without patterns instead of the whole branch crashing on a hard match miss.
    """
    payload = {
        "customer": {"url": url},
        "benchmark_summary": "",
        "video_count": 0,
        "video_analyses": [],
        "recommendations": {},
        "status": "no_reference_match",
        "note": reason,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def accepted_similar_count(out_dir: Path) -> int:
    similar_path = out_dir / "similar_landing_pages.json"
    if not similar_path.exists():
        return 0
    try:
        data = load_json(similar_path)
    except Exception:
        return 0
    return int(data.get("accepted_count") or 0)


def run_patterns(args: argparse.Namespace, out_dir: Path, benchmark_result: Path, env: Dict[str, str]) -> None:
    run([
        sys.executable, str(SCRIPT_DIR / "aeolus_ctr_top_videos.py"),
        "--benchmark-result", str(benchmark_result),
        "--output-dir", str(out_dir),
        "--limit", str(args.top_limit),
        "--min-impressions", str(args.top_min_impressions),
        "--min-clicks", str(args.top_min_clicks),
        "--last-sync-days", str(args.top_last_sync_days),
    ], cwd=ROOT, env=env)
    run([
        sys.executable, str(SCRIPT_DIR / "filter_similar_landing_pages.py"),
        "--benchmark-result", str(benchmark_result),
        "--top-videos", str(out_dir / "ctr_top50_videos.json"),
        "--customer-url", args.url,
        "--output-dir", str(out_dir),
        "--fetch-customer",
        "--provider", args.similarity_provider,
        "--model", args.similarity_model,
        "--aidp-retries", str(args.aidp_retries),
    ], cwd=ROOT, env=env)

    # No reference landing page cleared the similarity bar. This is the documented
    # "references not sufficiently matched" case: degrade gracefully to pattern-less
    # generation rather than downloading/analyzing an empty set and then failing.
    if accepted_similar_count(out_dir) <= 0:
        reason = (
            "No CTR Top reference landing page passed similarity filtering, so no "
            "reusable creative patterns were extracted. Generation proceeds from the "
            "landing-page brief only. To recover references, rerun with a stricter "
            "Aeolus context (e.g. aeolus_ctr_top_videos.py --strict-match-level account_l3) "
            "or a lower --threshold on filter_similar_landing_pages.py."
        )
        print(f"[url-pattern] {reason}", flush=True)
        write_empty_video_analysis(out_dir / "video_analysis.json", url=args.url, reason=reason)
        if (out_dir / "benchmark_result.json").exists():
            run([sys.executable, str(SCRIPT_DIR / "enrich_report.py"), "--output-dir", str(out_dir)], cwd=ROOT, env=env, allow_fail=True)
        return

    run([
        sys.executable, str(SCRIPT_DIR / "download_reference_videos.py"),
        "--similar-pages", str(out_dir / "similar_landing_pages.json"),
        "--output-dir", str(out_dir),
        "--max-videos", str(args.max_videos),
        "--workers", str(args.download_workers),
        "--retries", str(args.download_retries),
    ], cwd=ROOT, env=env)
    run([
        sys.executable, str(SCRIPT_DIR / "analyze_reference_videos.py"),
        "--download-manifest", str(out_dir / "download_manifest.json"),
        "--benchmark-result", str(benchmark_result),
        "--output-dir", str(out_dir),
        "--provider", args.analysis_provider,
        "--model", args.aidp_model,
        "--aidp-max-tokens", str(args.aidp_max_tokens),
        "--aidp-retries", str(args.aidp_retries),
        "--max-videos", str(args.max_videos),
        "--workers", str(args.analysis_workers),
    ], cwd=ROOT, env=env)
    if (out_dir / "benchmark_result.json").exists():
        run([sys.executable, str(SCRIPT_DIR / "enrich_report.py"), "--output-dir", str(out_dir)], cwd=ROOT, env=env, allow_fail=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run URL->adv/industry->CTR Top->reference-video pattern branch.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--adv-id", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--url-summary", default="", help="Optional URL crawl/caption-brief JSON/text for industry fallback classification")
    parser.add_argument("--strict-match-level", choices=["none", "account_l3", "account_l2_l3", "aic3", "aic2_aic3", "domain"], default="account_l3")
    parser.add_argument("--context-last-sync-days", type=int, default=30)
    parser.add_argument("--context-limit", type=int, default=200)
    parser.add_argument("--context-min-clicks", type=int, default=1)
    parser.add_argument("--context-min-impressions", type=int, default=1)
    parser.add_argument("--benchmark-last-sync-days", type=int, default=30)
    parser.add_argument("--industry-candidate-limit", type=int, default=300)
    parser.add_argument("--industry-min-impressions", type=int, default=1000)
    parser.add_argument("--industry-last-sync-days", type=int, default=30)
    parser.add_argument("--top-limit", type=int, default=50)
    parser.add_argument("--top-min-impressions", type=int, default=100)
    parser.add_argument("--top-min-clicks", type=int, default=1)
    parser.add_argument("--top-last-sync-days", type=int, default=1)
    parser.add_argument("--similarity-provider", choices=["aidp", "openai"], default="aidp")
    parser.add_argument("--similarity-model", default="gemini-2.5-pro")
    parser.add_argument("--analysis-provider", choices=["aidp", "openai"], default="aidp")
    parser.add_argument("--aidp-model", default="gemini-2.5-pro")
    parser.add_argument("--aidp-endpoint", default="https://aidp.bytedance.net/api/modelhub/online/multimodal/crawl")
    parser.add_argument("--aidp-max-tokens", type=int, default=64000)
    parser.add_argument("--aidp-retries", type=int, default=3)
    parser.add_argument("--max-videos", type=int, default=10)
    parser.add_argument("--download-workers", type=int, default=4)
    parser.add_argument("--download-retries", type=int, default=3)
    parser.add_argument("--analysis-workers", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    preflight_or_raise(args)
    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["BYTEDCLI_CLOUD_SITE"] = env.get("BYTEDCLI_CLOUD_SITE", "i18n")

    benchmark_result = run_full_benchmark(args, out_dir, env)
    if benchmark_result is None:
        benchmark_result = run_industry_fallback(args, out_dir, env)
    run_patterns(args, out_dir, benchmark_result, env)
    video_analysis = out_dir / "video_analysis.json"
    if not video_analysis.exists():
        # Safety net: never fail the parallel branch just because analysis produced
        # no file. Emit an empty, schema-compatible analysis so generation continues.
        reason = "Pattern branch finished without producing video_analysis.json; continuing pattern-less."
        print(f"[url-pattern] {reason}", flush=True)
        write_empty_video_analysis(video_analysis, url=args.url, reason=reason)
    try:
        status = str(load_json(video_analysis).get("status") or "ok")
    except Exception:
        status = "ok"
    print(json.dumps({"status": status, "output_dir": str(out_dir), "benchmark_result": str(benchmark_result), "video_analysis": str(video_analysis)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
