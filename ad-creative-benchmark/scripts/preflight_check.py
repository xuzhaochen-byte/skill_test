#!/usr/bin/env python3
"""Preflight dependency checker for ad-creative-benchmark workflows.

Checks only presence of commands/files/environment variables. It never prints
secret values.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


DEFAULT_MODELHUB_BASE_ENDPOINT = "https://aidp-i18ntt-sg.tiktok-row.net/api/modelhub/online/v2/crawl"
DEFAULT_INTERNAL_PYPI_INDEX = os.environ.get("INTERNAL_PYPI_INDEX", "https://bytedpypi.byted.org/simple/")

# Resolve skill root relative to this script's location.
# This script lives at <skill_root>/scripts/preflight_check.py, so the
# skill root is two levels up.  Fall back to CWD-relative paths so the
# old "run from project root with ad-creative-benchmark/ sibling" layout
# still works.
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPT_DIR.parent  # <skill_root>/


def _resolve_skill_file(*parts: str) -> Path:
    """Return the first existing path among skill-root-relative and CWD-relative."""
    candidates = [
        _SKILL_ROOT.joinpath(*parts),               # relative to script's skill root
        Path("ad-creative-benchmark").joinpath(*parts),  # CWD/ad-creative-benchmark/...
        Path(*parts),                                # CWD-relative (legacy)
    ]
    for p in candidates:
        if p.exists():
            return p
    # Return the skill-root path as the canonical location even if missing,
    # so the error message is deterministic.
    return candidates[0]


def bytedtos_available() -> bool:
    return importlib.util.find_spec("bytedtos") is not None


def install_bytedtos(index_url: str = DEFAULT_INTERNAL_PYPI_INDEX) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "bytedtos", "--index-url", index_url]
    print(f"[deps] installing bytedtos from internal index: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode == 0


def has_any_env(names: Iterable[str]) -> Tuple[bool, str]:
    present = [n for n in names if str(os.environ.get(n, "") or "").strip()]
    return bool(present), "/".join(present) if present else "/".join(names)


def add_check(checks: List[Tuple[str, bool, str]], name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))


def modelhub_configured() -> Tuple[bool, str]:
    endpoint_ok, endpoint_src = has_any_env(["MODELHUB_ENDPOINT", "GENERATION_PLANNER_ENDPOINT"])
    ak_ok, ak_src = has_any_env(["MODELHUB_AK", "MODELHUB_API_KEY", "MODELHUB_AK_LIST", "MODELHUB_API_KEYS"])
    if endpoint_ok and ak_ok:
        return True, f"configured by {endpoint_src} with key from {ak_src}"
    if endpoint_ok:
        return True, f"configured by {endpoint_src}; ensure endpoint already includes ak= or accepts auth without MODELHUB_AK"
    if ak_ok:
        return True, f"will build {DEFAULT_MODELHUB_BASE_ENDPOINT}?ak=... from {ak_src}"
    return False, "set MODELHUB_ENDPOINT/GENERATION_PLANNER_ENDPOINT and/or MODELHUB_AK/MODELHUB_API_KEY/MODELHUB_AK_LIST/MODELHUB_API_KEYS; AIDP_AK* is reserved for benchmark/pattern analysis"


def aidp_keys_configured() -> Tuple[bool, str]:
    ok, src = has_any_env(["AIDP_AK_LIST", "AIDP_API_KEYS", "AIDP_AK", "AIDP_API_KEY"])
    return ok, f"configured by {src}" if ok else "set AIDP_AK_LIST/AIDP_API_KEYS or AIDP_AK/AIDP_API_KEY"


def ark_keys_configured() -> Tuple[bool, str]:
    ok, src = has_any_env(["ARK_API_KEYS", "ARK_API_KEY"])
    return ok, f"configured by {src}" if ok else "set ARK_API_KEYS or ARK_API_KEY"


def image_gen_configured() -> Tuple[bool, str]:
    ok, src = has_any_env(["IMAGE_GEN_AK"])
    return ok, f"configured by {src}" if ok else "set IMAGE_GEN_AK"


def coze_configured() -> Tuple[bool, str]:
    ok, src = has_any_env(["COZE_API_TOKEN"])
    return ok, f"configured by {src}" if ok else "set COZE_API_TOKEN or run with --no-web-search / --web-search-provider duckduckgo"


def public_upload_configured() -> Tuple[bool, str]:
    ok, src = has_any_env(["PUBLIC_TOS_AK"])
    ok_sk, src_sk = has_any_env(["PUBLIC_TOS_SK"])
    if ok and ok_sk:
        return True, f"configured by {src} and {src_sk}"
    return False, "set PUBLIC_TOS_AK and PUBLIC_TOS_SK for humanaigc public CDN upload before TikTok upload/create"


def run(args: argparse.Namespace) -> int:
    checks: List[Tuple[str, bool, str]] = []
    workflows = set(args.workflow)
    if "all" in workflows:
        workflows = {"benchmark", "patterns", "url-generation"}

    need_bytedcli = bool(workflows & {"benchmark", "patterns"})
    if need_bytedcli:
        add_check(checks, "bytedcli command", shutil.which("bytedcli") is not None, "required for Aeolus benchmark and CTR Top queries")
        add_check(checks, "BYTEDCLI_CLOUD_SITE", bool(os.environ.get("BYTEDCLI_CLOUD_SITE", "i18n")), "defaults to i18n in scripts; export BYTEDCLI_CLOUD_SITE=i18n explicitly if needed")

    if "patterns" in workflows:
        downloader_path = _resolve_skill_file("preview_vid_downloader", "download_by_vid.py")
        add_check(checks, "reference video downloader", downloader_path.exists(), f"required for downloading Top reference videos (looked at {downloader_path})")
        if args.provider == "aidp":
            ok, detail = aidp_keys_configured()
            add_check(checks, "AIDP keys for URL industry fallback, similarity filtering, and MLLM video/pattern analysis", ok, detail)
        else:
            ok, detail = ark_keys_configured()
            add_check(checks, "ARK_API_KEY for OpenAI-compatible video analysis", ok, detail)
            add_check(checks, "ffmpeg command", shutil.which("ffmpeg") is not None, "required for frame sampling with provider=openai")
            add_check(checks, "sample.py", Path(args.sample_py).exists(), f"required at {args.sample_py} for provider=openai")

    if "url-generation" in workflows:
        ok, detail = modelhub_configured()
        add_check(checks, "ModelHub endpoint or MODELHUB_AK for URL review/planning/captions", ok, detail)
        if args.web_search_provider == "coze":
            ok, detail = coze_configured()
            add_check(checks, "Coze web search token for default URL enrichment", ok, detail)
        if args.need_ark:
            ok, detail = ark_keys_configured()
            add_check(checks, "Ark video generation keys", ok, detail)
            ok_model, src_model = has_any_env(["ARK_MODEL_NAME", "ARK_MODEL_NAMES"])
            add_check(checks, "Ark model names", True, f"configured by {src_model}" if ok_model else "optional; set ARK_MODEL_NAME/ARK_MODEL_NAMES when keys need paired models")
        if args.need_image_gen:
            ok, detail = image_gen_configured()
            add_check(checks, "image generation key", ok, detail)

    if args.need_public_upload or "campaign-upload" in workflows:
        ok, detail = public_upload_configured()
        add_check(checks, "public TOS upload credentials", ok, detail)
        index_url = getattr(args, "internal_pypi_index", "") or DEFAULT_INTERNAL_PYPI_INDEX
        if not bytedtos_available() and getattr(args, "install_deps", False):
            install_bytedtos(index_url)
        has_bytedtos = bytedtos_available()
        # Informational only (never fails preflight): the skill auto-installs
        # bytedtos at upload time, and the campaign step has a local-tunnel fallback.
        add_check(
            checks,
            "bytedtos SDK for public CDN upload",
            True,
            "importable" if has_bytedtos else f"not yet installed; the skill auto-installs it from {index_url} at upload time (or run this checker with --install-deps / use the local-tunnel fallback)",
        )

    failed = False
    for name, ok, detail in checks:
        status = "OK" if ok else "MISSING"
        print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
        failed = failed or not ok
    if failed:
        print("\nPreflight failed. Ask the user to authenticate/configure missing dependencies before starting the long workflow.", file=sys.stderr)
        return 1
    print("\nPreflight passed. Do not print or persist raw secret values in later logs/summaries.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Check required dependencies before running ad creative benchmark/generation workflows")
    ap.add_argument("--workflow", action="append", choices=["benchmark", "patterns", "url-generation", "campaign-upload", "all"], default=[], help="Workflow to check; pass multiple times. benchmark=report only, patterns=Top/video analysis, url-generation=URL/image/video generation, campaign-upload=public URL readiness for TikTok upload/create")
    ap.add_argument("--provider", choices=["aidp", "openai"], default="aidp", help="MLLM provider for reference video pattern analysis")
    ap.add_argument("--need-ark", action="store_true", help="Require Ark video generation credentials for --submit-ark")
    ap.add_argument("--need-image-gen", action="store_true", help="Require IMAGE_GEN_AK for --generate-image-assets")
    ap.add_argument("--need-public-upload", action="store_true", help="Require PUBLIC_TOS_AK/PUBLIC_TOS_SK for public CDN upload before TikTok upload-by-URL")
    ap.add_argument("--web-search-provider", choices=["coze", "duckduckgo"], default="coze", help="Supplemental web-search provider for URL generation preflight; default coze")
    ap.add_argument("--no-web-search", action="store_const", const="none", dest="web_search_provider", help="Skip web-search credential checks when the run will pass --no-web-search")
    ap.add_argument("--url-pattern-branch", action="store_true", help="Alias for URL generation plus benchmark/pattern branch; requires bytedcli and AIDP keys before long runs")
    ap.add_argument("--sample-py", default="sample.py", help="sample.py path when --provider openai")
    ap.add_argument("--internal-pypi-index", default=DEFAULT_INTERNAL_PYPI_INDEX, help="Internal PyPI index for installing bytedtos (default https://bytedpypi.byted.org/simple/)")
    ap.add_argument("--install-deps", action="store_true", help="Install missing bytedtos from the internal PyPI index during this check (for the public-upload/campaign-upload workflow)")
    args = ap.parse_args()
    if args.url_pattern_branch:
        args.workflow = sorted(set(args.workflow + ["url-generation", "benchmark", "patterns"]))
        args.provider = "aidp"
    if not args.workflow:
        args.workflow = ["all"]
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
