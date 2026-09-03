#!/usr/bin/env python3
"""One-command URL/structured-row to Ark image-to-video pipeline.

Pipeline modes:
1. Use an existing structured crawler CSV via --structured-input.
2. Or crawl a CSV/URL first via --raw-input/--url, then build caption and submit.

Every stage writes reviewable intermediate files and timing metrics.
"""
from __future__ import annotations

import argparse
import atexit
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# Allow running this file directly without installing as a package.
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import ark_client
import caption_builder
import image_asset_generator

SKILL_DIR = THIS_DIR.parent
DEFAULT_STRUCTURED_INPUT = SKILL_DIR / "outputs" / "url_crawl_compare_visual_text_rerun_full.csv"
DEFAULT_OUT_DIR = SKILL_DIR / "runs"
DEFAULT_LLM_RETRY_ATTEMPTS = int(os.environ.get("LLM_RETRY_ATTEMPTS", "20"))
DEFAULT_LLM_RETRY_SLEEP_SECONDS = float(os.environ.get("LLM_RETRY_SLEEP_SECONDS", "10"))
DEFAULT_MODELHUB_BASE_ENDPOINT = "https://aidp-i18ntt-sg.tiktok-row.net/api/modelhub/online/v2/crawl"
DEFAULT_PUBLIC_UPLOAD_SITE_PACKAGES = "/Users/bytedance/Desktop/agentic_ad_creation/sparrow/venv/lib/python3.9/site-packages"
DEFAULT_PUBLIC_TOS_BUCKET = "humanaigc-ads-data"
DEFAULT_PUBLIC_TOS_ENDPOINT = "tos-cn-north.byted.org"
DEFAULT_PUBLIC_TOS_SERVICE = "toutiao.tos.tosapi"
DEFAULT_PUBLIC_TOS_CLUSTER = "default"
DEFAULT_PUBLIC_TOS_IDC = "default"
DEFAULT_PUBLIC_TOS_CDN_PREFIX = "https://lf-ads-humanaigc.bytecdn.com/obj/humanaigc-ads-data"
DEFAULT_INTERNAL_PYPI_INDEX = os.environ.get("INTERNAL_PYPI_INDEX", "https://bytedpypi.byted.org/simple/")
NINE_SIXTEEN_RATIO = 9 / 16


class StageTimer:
    def __init__(self) -> None:
        self.metrics: Dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        print(f"\n[stage:start] {name}", flush=True)
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.metrics[name] = self.metrics.get(name, 0.0) + elapsed
            print(f"[stage:done] {name}: {elapsed:.2f}s", flush=True)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_optional_sys_path(path_value: str) -> None:
    path_value = norm_text(path_value)
    if path_value and Path(path_value).exists() and path_value not in sys.path:
        sys.path.insert(0, path_value)


def install_bytedtos(index_url: str = DEFAULT_INTERNAL_PYPI_INDEX) -> bool:
    """Install the internal bytedtos SDK from the internal PyPI index.

    Returns True on success. Used both by lazy auto-install and by
    preflight_check.py --install-deps so the skill bundles its own dependency
    bootstrap instead of relying on the user to pip-install manually.
    """
    cmd = [sys.executable, "-m", "pip", "install", "bytedtos", "--index-url", index_url]
    print(f"[deps] installing bytedtos from internal index: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd)
    return proc.returncode == 0


def _load_bytedtos(args: argparse.Namespace):
    """Import bytedtos, auto-installing it from the internal PyPI index if absent.

    The public CDN uploader needs the internal bytedtos SDK. Rather than failing
    when it is not pre-installed, the skill installs it on demand from
    ``--internal-pypi-index`` (default https://bytedpypi.byted.org/simple/). Set
    ``--no-auto-install-deps`` (or env SKILL_NO_AUTO_INSTALL=1) to disable.
    """
    _append_optional_sys_path(getattr(args, "public_upload_site_packages", ""))
    try:
        import bytedtos  # type: ignore
        return bytedtos
    except ImportError:
        auto = bool(getattr(args, "auto_install_deps", True)) and not str(os.environ.get("SKILL_NO_AUTO_INSTALL", "")).strip()
        index_url = norm_text(getattr(args, "internal_pypi_index", "")) or DEFAULT_INTERNAL_PYPI_INDEX
        if not auto:
            raise RuntimeError(
                f"bytedtos is required for public CDN upload but is not installed and auto-install is disabled. "
                f"Install it with: pip install bytedtos --index-url={index_url}"
            )
        if not install_bytedtos(index_url):
            raise RuntimeError(
                f"bytedtos auto-install from {index_url} failed. Install it manually "
                f"(pip install bytedtos --index-url={index_url}) or use the local-tunnel "
                f"fallback in references/tiktok_campaign_upload_workflow.md."
            )
        import importlib
        importlib.invalidate_caches()
        import bytedtos  # type: ignore
        return bytedtos


def _public_upload_humanaigc(data: bytes, key_name: str, args: argparse.Namespace) -> str:
    """Upload bytes to the humanaigc public TOS bucket and return a CDN URL.

    Credentials are read from environment variables by default. The built-in
    defaults match the internal helper provided for this workflow, but callers
    can override every field without code changes.
    """
    bytedtos = _load_bytedtos(args)
    bucket = norm_text(getattr(args, "public_tos_bucket", "")) or DEFAULT_PUBLIC_TOS_BUCKET
    endpoint = norm_text(getattr(args, "public_tos_endpoint", "")) or DEFAULT_PUBLIC_TOS_ENDPOINT
    service = norm_text(getattr(args, "public_tos_service", "")) or DEFAULT_PUBLIC_TOS_SERVICE
    cluster = norm_text(getattr(args, "public_tos_cluster", "")) or DEFAULT_PUBLIC_TOS_CLUSTER
    idc = norm_text(getattr(args, "public_tos_idc", "")) or DEFAULT_PUBLIC_TOS_IDC
    ak = norm_text(getattr(args, "public_tos_ak", "")) or os.environ.get("PUBLIC_TOS_AK", "")
    sk = norm_text(getattr(args, "public_tos_sk", "")) or os.environ.get("PUBLIC_TOS_SK", "")
    if not ak or not sk:
        raise RuntimeError("PUBLIC_TOS_AK and PUBLIC_TOS_SK are required for public TOS upload")
    credentials = bytedtos.StaticCredentials(ak, sk)
    client = bytedtos.Client(
        bucket,
        credentials,
        service=service,
        cluster=cluster,
        endpoint=endpoint,
        idc=idc,
        timeout=int(getattr(args, "public_tos_timeout", 120) or 120),
        connect_timeout=int(getattr(args, "public_tos_connect_timeout", 60) or 60),
    )
    client.put_object(key_name, data)
    cdn_prefix = (norm_text(getattr(args, "public_tos_cdn_prefix", "")) or DEFAULT_PUBLIC_TOS_CDN_PREFIX).rstrip("/")
    return f"{cdn_prefix}/{quote(key_name.lstrip('/'))}"


def _public_upload_sparrow(data: bytes, key_name: str, args: argparse.Namespace) -> str:
    """Fallback to the older Sparrow uploader when explicitly requested."""
    python_path = norm_text(getattr(args, "public_upload_python_path", ""))
    _append_optional_sys_path(python_path)
    if not norm_text(getattr(args, "public_upload_site_packages", "")) and python_path:
        sparrow_root = Path(python_path).parent
        version_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
        _append_optional_sys_path(str(sparrow_root / "venv" / "lib" / version_tag / "site-packages"))
    from logic.e2e_video_gen.utils.upload_tos import upload_tos_public  # type: ignore

    return upload_tos_public(data=data, key_name=key_name, return_url=True)


def upload_bytes_to_public_url(data: bytes, key_name: str, args: argparse.Namespace) -> str:
    provider = norm_text(getattr(args, "public_upload_provider", "humanaigc")).lower()
    if provider in {"humanaigc", "humanaigc-ads-data", "tos"}:
        return _public_upload_humanaigc(data, key_name, args)
    if provider == "sparrow":
        return _public_upload_sparrow(data, key_name, args)
    raise ValueError(f"Unsupported public upload provider: {provider}")


def upload_file_to_public_url(path: Path, args: argparse.Namespace, asset_type: str = "asset") -> Dict[str, Any]:
    """Upload a generated file to a public URL, without failing the main job on upload errors."""
    record: Dict[str, Any] = {"ok": False, "path": str(path), "asset_type": asset_type}
    if not getattr(args, "upload_generated_assets", True):
        record.update({"skipped": True, "reason": "upload_generated_assets_disabled"})
        return record
    if not path.exists() or not path.is_file():
        record["error"] = "file not found"
        return record

    retries = max(1, int(getattr(args, "public_upload_retries", 3) or 1))
    sleep_seconds = max(0.0, float(getattr(args, "public_upload_retry_sleep_seconds", 3) or 0))
    key_prefix = norm_text(getattr(args, "public_upload_key_prefix", "creative_url_video_pipeline/generated")).strip("/")
    data = path.read_bytes()
    digest = hashlib.md5(data).hexdigest()
    suffix = path.suffix.lower() or ".bin"
    safe_asset_type = re.sub(r"[^a-zA-Z0-9_.-]+", "_", asset_type).strip("_") or "asset"
    key_name = f"{key_prefix}/{safe_asset_type}/{digest}{suffix}" if key_prefix else f"{safe_asset_type}/{digest}{suffix}"
    record.update({"bytes": len(data), "key_name": key_name, "attempts": []})

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            public_url = upload_bytes_to_public_url(data=data, key_name=key_name, args=args)
            record["attempts"].append({"attempt": attempt, "ok": bool(public_url)})
            if public_url:
                record.update({"ok": True, "public_url": public_url, "attempt_count": attempt})
                return record
            last_error = "empty public url returned"
        except Exception as exc:  # noqa: BLE001 - persisted for review.
            last_error = f"{type(exc).__name__}: {exc}"
            record["attempts"].append({"attempt": attempt, "ok": False, "error": last_error[:1000]})
        if attempt < retries and sleep_seconds > 0:
            time.sleep(sleep_seconds)
    record.update({"error": last_error or "upload failed", "attempt_count": retries})
    return record


def collect_public_asset_urls(results: Dict[str, Any]) -> Dict[str, Any]:
    image_assets: List[Dict[str, Any]] = []
    videos: List[Dict[str, Any]] = []
    for item in results.get("image_assets", []) or []:
        plan = item.get("plan", {}) if isinstance(item, dict) else {}
        for upload in item.get("public_uploads", []) or []:
            if upload.get("ok") and upload.get("public_url"):
                image_assets.append({
                    "index": plan.get("index"),
                    "style": plan.get("style"),
                    "scene_type": plan.get("scene_type"),
                    "path": upload.get("path"),
                    "public_url": upload.get("public_url"),
                })
    for item in results.get("videos", []) or []:
        plan = item.get("plan", {}) if isinstance(item, dict) else {}
        upload = item.get("public_upload", {}) if isinstance(item, dict) else {}
        if upload.get("ok") and upload.get("public_url"):
            videos.append({
                "index": plan.get("index"),
                "concept": plan.get("concept"),
                "path": upload.get("path"),
                "public_url": upload.get("public_url"),
                "ark_source_url": item.get("video_url"),
                "task_id": item.get("task_id"),
            })
    return {"image_assets": image_assets, "videos": videos}


def first_modelhub_ak_from_env() -> str:
    for name in ["MODELHUB_AK", "MODELHUB_API_KEY"]:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
    for name in ["MODELHUB_AK_LIST", "MODELHUB_API_KEYS"]:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            first = re.split(r"[,;\n]+", value, maxsplit=1)[0].strip()
            if first:
                return first
    return ""


def endpoint_with_ak(endpoint: str, ak: str) -> str:
    endpoint = str(endpoint or "").strip()
    ak = str(ak or "").strip()
    if not endpoint or not ak or "ak=" in endpoint:
        return endpoint
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}ak={ak}"


def default_modelhub_endpoint_from_env() -> str:
    modelhub_ak = first_modelhub_ak_from_env()
    for name in ["MODELHUB_ENDPOINT", "GENERATION_PLANNER_ENDPOINT"]:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return endpoint_with_ak(value, modelhub_ak)
    if modelhub_ak:
        return f"{DEFAULT_MODELHUB_BASE_ENDPOINT}?ak={modelhub_ak}"
    return ""


def is_retryable_llm_http_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def llm_retry_attempts(args: argparse.Namespace) -> int:
    return max(1, int(getattr(args, "llm_retry_attempts", DEFAULT_LLM_RETRY_ATTEMPTS) or 1))


def llm_retry_sleep_seconds(args: argparse.Namespace) -> float:
    return max(0.0, float(getattr(args, "llm_retry_sleep_seconds", DEFAULT_LLM_RETRY_SLEEP_SECONDS) or 0.0))


def post_llm_json_with_sleep_retry(
    endpoint: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: int,
    *,
    attempts: int,
    sleep_seconds: float,
    validate: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """POST one LLM request with sleep/retry and optional response validation.

    Validation errors are retried because ModelHub may occasionally return an
    empty/length-truncated or non-JSON answer even when HTTP succeeds. Local
    fallbacks are intentionally not used; exhausting retries raises loudly.
    """
    last_error: Optional[BaseException] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            try:
                raw = response.json()
            except Exception:
                raw = {"raw_text": response.text}
            raw["_http_status"] = response.status_code
            raw["_llm_retry_attempt"] = attempt
            if response.status_code >= 400:
                if is_retryable_llm_http_status(response.status_code):
                    raise RuntimeError(f"retryable LLM HTTP {response.status_code}: {str(raw)[:500]}")
                response.raise_for_status()
            if validate:
                validate(raw)
            return raw
        except Exception as exc:  # noqa: BLE001 - retry then fail loudly with last exception.
            last_error = exc
            if attempt >= max(1, attempts):
                raise RuntimeError(f"LLM call failed after {attempt} attempt(s): {exc}") from exc
            print(f"[llm:retry] attempt {attempt}/{attempts} failed: {exc}; sleep {sleep_seconds}s", flush=True)
            time.sleep(sleep_seconds)
    raise RuntimeError(f"LLM call failed: {last_error}")


def write_single_url_csv(path: Path, url: str, case_id: str = "single_url") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "raw_url"])
        writer.writeheader()
        writer.writerow({"case_id": case_id, "raw_url": url})


def run_subprocess(cmd: List[str], cwd: Path) -> None:
    print("$ " + " ".join(str(x) for x in cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd), text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with exit code {proc.returncode}: {' '.join(cmd)}")


def start_parallel_benchmark_process(args: argparse.Namespace, run_dir: Path) -> Optional[subprocess.Popen]:
    command = norm_text(getattr(args, "parallel_benchmark_command", ""))
    if not command:
        return None
    if not norm_text(getattr(args, "benchmark_video_analysis", "")) and not norm_text(getattr(args, "benchmark_output_dir", "")):
        print(
            "[parallel-benchmark] warning: --parallel-benchmark-command was supplied without "
            "--benchmark-output-dir or --benchmark-video-analysis; generation will not wait for or load patterns unless a path is provided.",
            flush=True,
        )
    cwd = Path(getattr(args, "parallel_benchmark_cwd", "") or Path.cwd())
    stdout_path = run_dir / "parallel_benchmark_stdout.log"
    stderr_path = run_dir / "parallel_benchmark_stderr.log"
    meta = {
        "command": command,
        "cwd": str(cwd),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    dump_json(run_dir / "parallel_benchmark_process.json", meta)
    stdout_f = stdout_path.open("w", encoding="utf-8")
    stderr_f = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=str(cwd), shell=True, stdout=stdout_f, stderr=stderr_f, text=True)
    # Keep file objects reachable so they are not garbage-collected while the process runs.
    proc._codex_stdout_file = stdout_f  # type: ignore[attr-defined]
    proc._codex_stderr_file = stderr_f  # type: ignore[attr-defined]
    atexit.register(close_parallel_benchmark_files, proc)
    print(f"[parallel-benchmark] started pid={proc.pid} cwd={cwd}", flush=True)
    return proc


def close_parallel_benchmark_files(proc: Optional[subprocess.Popen]) -> None:
    if not proc:
        return
    for attr in ["_codex_stdout_file", "_codex_stderr_file"]:
        handle = getattr(proc, attr, None)
        try:
            if handle:
                handle.close()
        except Exception:
            pass


def wait_for_parallel_benchmark_if_needed(args: argparse.Namespace, expected_path: Path) -> bool:
    proc = getattr(args, "_parallel_benchmark_proc", None)
    wait_seconds = max(0, int(getattr(args, "benchmark_wait_seconds", 0) or 0))
    fail_policy = getattr(args, "parallel_benchmark_fail_policy", "warn")
    if expected_path.exists():
        return True
    if not proc and wait_seconds <= 0:
        return False
    deadline = time.perf_counter() + wait_seconds
    while True:
        if expected_path.exists():
            return True
        rc = proc.poll() if proc else None
        if rc is not None:
            close_parallel_benchmark_files(proc)
            if rc != 0:
                msg = f"parallel benchmark command failed with exit code {rc}; expected {expected_path}"
                if fail_policy == "fail":
                    raise RuntimeError(msg)
                print(f"[parallel-benchmark] {msg}; continue without benchmark patterns", flush=True)
            return expected_path.exists()
        if time.perf_counter() >= deadline:
            msg = f"timed out waiting {wait_seconds}s for benchmark patterns: {expected_path}"
            if fail_policy == "fail":
                raise TimeoutError(msg)
            print(f"[parallel-benchmark] {msg}; continue without benchmark patterns", flush=True)
            return False
        time.sleep(5)


def run_crawler(args: argparse.Namespace, run_dir: Path) -> Path:
    if args.url:
        raw_input = run_dir / "single_url_input.csv"
        write_single_url_csv(raw_input, args.url, args.effective_case_id)
    else:
        raw_input = Path(args.raw_input)
    output_csv = Path(args.crawl_output) if args.crawl_output else run_dir / "structured_crawl.csv"
    crawler = THIS_DIR / "url_crawl_compare.py"
    cmd = [
        sys.executable,
        str(crawler),
        "--input",
        str(raw_input),
        "--output",
        str(output_csv),
        "--format",
        "csv",
        "--image-mode",
        args.image_mode,
        "--visual-candidate-limit",
        str(args.visual_candidate_limit),
        "--max-creative-images",
        str(args.max_creative_images),
        "--text-heavy-policy",
        args.text_heavy_policy,
        "--structured-review",
        args.structured_review,
    ]
    if args.structured_review == "modelhub":
        cmd += [
            "--structured-review-model",
            args.structured_review_model,
            "--structured-review-timeout",
            str(args.structured_review_timeout),
            "--structured-review-max-tokens",
            str(args.structured_review_max_tokens),
            "--llm-retry-attempts",
            str(args.llm_retry_attempts),
            "--llm-retry-sleep-seconds",
            str(args.llm_retry_sleep_seconds),
        ]
        if args.structured_review_endpoint:
            cmd += ["--structured-review-endpoint", args.structured_review_endpoint]
        if args.structured_review_logid:
            cmd += ["--structured-review-logid", args.structured_review_logid]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    if args.visual_review != "none":
        cmd += ["--visual-review", args.visual_review, "--visual-model", args.visual_model]
        cmd += ["--visual-review-batch-size", str(args.visual_review_batch_size)]
        cmd += ["--opaque-shopify-recall-limit", str(args.opaque_shopify_recall_limit)]
    if args.download_visual_candidates:
        cmd += ["--download-visual-candidates", "--download-images-dir", str(run_dir / "crawler_visual_candidates")]
    elif args.visual_review in {"openai", "modelhub"}:
        cmd += ["--download-images-dir", str(run_dir / "crawler_visual_candidates")]
    if args.write_image_debug_files:
        cmd.append("--write-image-debug-files")
    if args.enable_web_search:
        cmd += [
            "--enable-web-search",
            "--web-search-provider",
            args.web_search_provider,
            "--web-fetch-mode",
            args.web_fetch_mode,
            "--web-search-limit",
            str(args.web_search_limit),
            "--web-image-limit",
            str(args.web_image_limit),
        ]
        if args.coze_token:
            cmd += ["--coze-token", args.coze_token]
    run_subprocess(cmd, cwd=THIS_DIR)
    return output_csv


def safe_suffix_from_url(url: str) -> str:
    low = url.split("?", 1)[0].lower()
    for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
        if low.endswith(ext):
            return ext
    return ".img"


def image_dimensions(path: Path) -> Dict[str, int]:
    with Image.open(path) as img:
        return {"width": int(img.width), "height": int(img.height)}


def is_ratio_close(width: int, height: int, target_ratio: float = NINE_SIXTEEN_RATIO, tolerance: float = 0.015) -> bool:
    if width <= 0 or height <= 0:
        return False
    return abs((width / height) - target_ratio) <= tolerance


def pad_image_to_9_16(src_path: Path, out_path: Path, fill: str = "blur") -> Dict[str, Any]:
    """Create a 9:16 padded version of a selected original image."""
    with Image.open(src_path) as raw_img:
        img = ImageOps.exif_transpose(raw_img).convert("RGB")
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0:
            raise RuntimeError("invalid image dimensions")
        if src_w / src_h > NINE_SIXTEEN_RATIO:
            target_w = src_w
            target_h = int(round(src_w / NINE_SIXTEEN_RATIO))
        else:
            target_h = src_h
            target_w = int(round(src_h * NINE_SIXTEEN_RATIO))
        target_w = max(target_w, src_w)
        target_h = max(target_h, src_h)
        if fill == "blur":
            bg = ImageOps.fit(img, (target_w, target_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=max(12, int(max(target_w, target_h) * 0.018))))
            bg = ImageEnhance.Brightness(bg).enhance(0.82)
        else:
            bg = Image.new("RGB", (target_w, target_h), color=(245, 245, 245))
        x = (target_w - src_w) // 2
        y = (target_h - src_h) // 2
        bg.paste(img, (x, y))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(out_path, format="JPEG", quality=94)
        return {
            "path": str(out_path),
            "width": target_w,
            "height": target_h,
            "source_width": src_w,
            "source_height": src_h,
            "ratio": "9:16",
            "padded": True,
            "padding_fill": fill,
            "asset_type": "selected_original_padded",
        }


def make_selected_original_9_16_assets(image_downloads: List[Dict[str, Any]], out_dir: Path, fill: str = "blur") -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []
    for item in image_downloads:
        path_s = item.get("path")
        if not item.get("ok") or not path_s or not Path(path_s).exists():
            continue
        src_path = Path(path_s)
        try:
            dims = image_dimensions(src_path)
            width = dims["width"]
            height = dims["height"]
            needs_padding = not is_ratio_close(width, height)
            base = {
                "index": item.get("index"),
                "url": item.get("url"),
                "source_path": str(src_path),
                "source_width": width,
                "source_height": height,
                "source_aspect_ratio": round(width / height, 4) if height else None,
                "source_is_9_16": not needs_padding,
                "asset_type": "selected_original_source",
            }
            if needs_padding:
                out_path = out_dir / f"selected_original_{int(item.get('index') or len(assets)+1):02d}_9x16_padded.jpg"
                padded = pad_image_to_9_16(src_path, out_path, fill=fill)
                assets.append({**base, **padded, "source_asset_kind": "padded_from_selected_original"})
            else:
                assets.append({**base, "path": str(src_path), "width": width, "height": height, "padded": False, "ratio": "9:16", "source_asset_kind": "selected_original_already_9x16"})
        except Exception as exc:  # noqa: BLE001 - keep per-asset failure reviewable.
            assets.append({
                "index": item.get("index"),
                "url": item.get("url"),
                "source_path": path_s,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "asset_type": "selected_original_9x16_failed",
            })
    return assets


def safe_video_suffix_from_url(url: str) -> str:
    low = url.split("?", 1)[0].lower()
    for ext in [".mp4", ".mov", ".webm", ".m3u8"]:
        if low.endswith(ext):
            return ext
    return ".mp4"


def ffprobe_video_metadata(path: Path, timeout: int = 30) -> Dict[str, Any]:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found; cannot verify video dimensions for 9:16 filtering")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"ffprobe exited {proc.returncode}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") if isinstance(data, dict) else []
    stream = streams[0] if isinstance(streams, list) and streams else {}
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration_s = norm_text(stream.get("duration"))
    return {
        "width": width,
        "height": height,
        "duration": float(duration_s) if duration_s else None,
        "aspect_ratio": round(width / height, 4) if height else None,
        "is_9_16": is_ratio_close(width, height),
    }


def download_one_video(index: int, url: str, out_dir: Path, timeout: int, retries: int, sleep_base: float) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = safe_video_suffix_from_url(url)
    out_path = out_dir / f"landing_video_{index:02d}{suffix}"
    last_error = ""
    if "youtube" in url.lower() or "vimeo" in url.lower() or suffix == ".m3u8":
        return {"index": index, "url": url, "ok": False, "path": str(out_path), "error": "unsupported video download URL type for direct 9:16 filtering"}
    for attempt in range(1, retries + 2):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
            response.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            if out_path.stat().st_size <= 0:
                raise RuntimeError("empty video response")
            meta = ffprobe_video_metadata(out_path)
            if not meta.get("is_9_16"):
                return {"index": index, "url": url, "ok": False, "downloaded": True, "path": str(out_path), "bytes": out_path.stat().st_size, "reject_reason": "not_9_16", **meta}
            return {"index": index, "url": url, "ok": True, "downloaded": True, "path": str(out_path), "bytes": out_path.stat().st_size, **meta}
        except Exception as exc:  # noqa: BLE001 - stored for debug output.
            last_error = str(exc)
            if attempt <= retries:
                time.sleep(sleep_base * attempt)
    return {"index": index, "url": url, "ok": False, "path": str(out_path), "error": last_error, "attempts": retries + 1}


def download_landing_page_videos_9_16(video_urls: List[str], out_dir: Path, workers: int = 4, timeout: int = 60, retries: int = 2, sleep_base: float = 1.5) -> List[Dict[str, Any]]:
    if not video_urls:
        return []
    max_workers = max(1, min(workers, len(video_urls)))
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(download_one_video, i, url, out_dir, timeout, retries, sleep_base) for i, url in enumerate(video_urls, 1)]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: int(item.get("index") or 0))


def landing_page_video_urls_from_row(row: Dict[str, str]) -> List[str]:
    videos = caption_builder.safe_json_loads(row.get("videos_json"), [])
    if not isinstance(videos, list):
        return []
    return [norm_text(v) for v in videos if norm_text(v)]


def download_one_image(index: int, url: str, out_dir: Path, timeout: int, retries: int, sleep_base: float) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = safe_suffix_from_url(url)
    out_path = out_dir / f"image_{index:02d}{suffix}"
    last_error = ""
    for attempt in range(1, retries + 2):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            if not response.content:
                raise RuntimeError("empty response")
            out_path.write_bytes(response.content)
            return {"index": index, "url": url, "ok": True, "path": str(out_path), "bytes": len(response.content), "attempts": attempt}
        except Exception as exc:  # noqa: BLE001 - stored for debug output.
            last_error = str(exc)
            if attempt <= retries:
                time.sleep(sleep_base * attempt)
    return {"index": index, "url": url, "ok": False, "path": str(out_path), "error": last_error, "attempts": retries + 1}


def download_selected_images(image_urls: List[str], out_dir: Path, workers: int = 6, timeout: int = 20, retries: int = 2, sleep_base: float = 1.5) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not image_urls:
        return results
    max_workers = max(1, min(workers, len(image_urls)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(download_one_image, i, url, out_dir, timeout, retries, sleep_base) for i, url in enumerate(image_urls, 1)]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item["index"])


def write_caption_outputs(run_dir: Path, case_id: str, brief: Dict[str, Any], generation_prompt: str, final_caption: str) -> Dict[str, Path]:
    paths = {
        "brief": run_dir / f"{case_id}_caption_brief.json",
        "generation_prompt": run_dir / f"{case_id}_caption_generation_prompt.txt",
        "caption": run_dir / f"{case_id}_ark_caption.txt",
        "payload_content": run_dir / f"{case_id}_ark_payload_content.json",
    }
    dump_json(paths["brief"], brief)
    paths["generation_prompt"].write_text(generation_prompt, encoding="utf-8")
    paths["caption"].write_text(final_caption, encoding="utf-8")
    dump_json(paths["payload_content"], {
        "content": [{"type": "text", "text": final_caption}] + [
            {"type": "image_url", "image_url": {"url": asset["url"]}, "role": "reference_image"}
            for asset in brief["selected_images"]
        ]
    })
    return paths


def parse_csv_list(value: str) -> List[str]:
    return [item.strip() for item in (value or "").replace("\n", ",").split(",") if item.strip()]


def resolve_ark_key_model_pairs(args: argparse.Namespace) -> List[Dict[str, str]]:
    """Pair Ark API keys with model names by list index.

    `--ark-api-keys` / ARK_API_KEYS can contain comma- or newline-separated
    keys. `--ark-model-names` / ARK_MODEL_NAME follows the same format and is
    mapped by index to the key list. If no model list is provided, the legacy
    single `--ark-model` value is used for every key. If exactly one model name
    is provided, it is also used for every key for convenience.
    """
    keys = parse_csv_list(args.ark_api_keys) or parse_csv_list(os.environ.get("ARK_API_KEYS", ""))
    if not keys:
        single = os.environ.get("ARK_API_KEY", "")
        if single:
            keys = [single]
    model_names = parse_csv_list(getattr(args, "ark_model_names", ""))
    if not model_names:
        model_names = parse_csv_list(os.environ.get("ARK_MODEL_NAME", "")) or parse_csv_list(os.environ.get("ARK_MODEL_NAMES", ""))
    if not model_names:
        model_names = [args.ark_model]
    pairs: List[Dict[str, str]] = []
    for idx, key in enumerate(keys):
        if len(model_names) == 1:
            model = model_names[0]
        elif idx < len(model_names):
            model = model_names[idx]
        else:
            model = args.ark_model
        pairs.append({"api_key": key, "model": model})
    return pairs


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def uniq_keep_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        item = norm_text(item)
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def extract_chat_content(response_json: Dict[str, Any]) -> str:
    """Extract text content from common chat-completion gateway shapes."""
    try:
        content = response_json["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return norm_text(content)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return norm_text("\n".join(parts))
    except Exception:
        pass
    for key in ("content", "text", "output", "answer"):
        if isinstance(response_json.get(key), str):
            return norm_text(response_json[key])
    data = response_json.get("data")
    if isinstance(data, dict):
        return extract_chat_content(data)
    return ""


def parse_json_object_from_text(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def redact_key(key: str) -> str:
    if not key:
        return ""
    return key[:6] + "..." + key[-4:] if len(key) > 12 else "[REDACTED]"


def ark_error_text(exc: Exception) -> str:
    """Return the most useful Ark error body/text without exposing secrets."""
    body = getattr(exc, "body", "")
    return norm_text(body) or norm_text(str(exc))


def is_ark_input_sensitive_error(exc: Exception) -> bool:
    """True when Ark rejected the current payload for sensitive input media.

    Retrying the same image payload/key cannot fix these 400s. The practical
    recovery is to build a safer payload, for example removing reference images
    that contain identifiable faces/people.
    """
    text = ark_error_text(exc).lower()
    return any(token.lower() in text for token in [
        "InputImageSensitiveContentDetected",
        "PrivacyInformation",
        "input image may contain real person",
        "SensitiveContentDetected",
    ])


def select_reference_files(image_downloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for item in image_downloads:
        path = item.get("path")
        if item.get("ok") and path and Path(path).exists():
            refs.append(item)
    return refs


def local_image_has_face(path_s: str) -> bool:
    """Best-effort local face detector for Ark reference safety.

    This intentionally stays optional and conservative: if OpenCV is missing or
    the image cannot be read, return False so the LLM/image funnel remains the
    source of semantic truth. When faces are detected, we avoid using that image
    as an Ark video reference because Ark may reject identifiable people.
    """
    if not path_s:
        return False
    try:
        import cv2  # type: ignore
    except Exception:
        return False
    try:
        img = cv2.imread(str(path_s))
        if img is None:
            return False
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            return False
        min_side = max(24, int(min(img.shape[:2]) * 0.045))
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(min_side, min_side))
        if len(faces) > 0:
            return True
        profile_path = cv2.data.haarcascades + "haarcascade_profileface.xml"
        profile_cascade = cv2.CascadeClassifier(profile_path)
        if not profile_cascade.empty():
            profiles = profile_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(min_side, min_side))
            return len(profiles) > 0
    except Exception:
        return False
    return False


def local_image_has_full_person(path_s: str) -> bool:
    """Best-effort full-body detector used only as Ark safety metadata."""
    if not path_s:
        return False
    try:
        import cv2  # type: ignore
    except Exception:
        return False
    try:
        img = cv2.imread(str(path_s))
        if img is None:
            return False
        h, w = img.shape[:2]
        if max(h, w) > 900:
            scale = 900 / max(h, w)
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        rects, weights = hog.detectMultiScale(img, winStride=(8, 8), padding=(16, 16), scale=1.05)
        return any(float(weight) > 0.8 for weight in weights)
    except Exception:
        return False


def default_asset_style_specs() -> List[Dict[str, str]]:
    """Recommended image-asset scene styles for the LLM planner.

    These are suggestions only. They are intentionally passed as a catalog for
    the planner to reason over; they are not a local rule plan and they are not
    mandatory choices.
    """
    return [
        {
            "style": "ugc_lifestyle",
            "intent": "ordinary creator lifestyle photo; product integrated into a real daily setting; low-ad native TikTok feel",
        },
        {
            "style": "routine_scene",
            "intent": "daily routine use moment; hands/body/environment show how the product fits into life",
        },
        {
            "style": "creator_fit_check",
            "intent": "mirror selfie or outfit/fit-check style when the product is wearable; casual phone-shot framing",
        },
        {
            "style": "hands_closeup",
            "intent": "close-up use moment focused on hands/body crop, action, texture, or practical interaction with the referenced product",
        },
        {
            "style": "outdoor_street",
            "intent": "candid outdoor or street-style moment with natural movement and overseas creator energy",
        },
        {
            "style": "variant_or_bundle_grid",
            "intent": "clean collection/flat-lay image using 2-4 uploaded reference items only; useful for evidenced variants or bundles",
        },
        {
            "style": "collage_grid",
            "intent": "simple TikTok-native collage/grid using evidenced product views or real-life scene crops; avoid fake variants",
        },
        {
            "style": "infographic",
            "intent": "minimal product-in-use explanatory image only when exact supported text exists; avoid dense labels and invented claims",
        },
        {
            "style": "promo_poster",
            "intent": "bold promotional poster only when discount/offer text is explicitly supported by the landing page or image text sources",
        },
        {
            "style": "meme_native",
            "intent": "casual TikTok-native humorous/meme-style image only when the category is safe for humor and product remains visible",
        },
    ]



def is_combination_image_style(style: str) -> bool:
    return norm_text(style).lower() in {"variant_or_bundle_grid", "bundle_grid", "variant_grid", "collection_grid", "multi_variant_grid", "multi_item_grid"}



def compact_reference_catalog(brief: Dict[str, Any], reference_files: List[Dict[str, Any]], image_urls: List[str]) -> List[Dict[str, Any]]:
    """Build an indexed, reviewable reference catalog for generation planning.

    Image assets need local files as references; Ark video needs URLs. This
    catalog keeps both identifiers aligned so a planner can choose by index.
    """
    by_url: Dict[str, Dict[str, Any]] = {}
    for asset in brief.get("selected_images", []) or []:
        url = norm_text(asset.get("url"))
        if url:
            by_url[url] = asset
    local_by_url = {norm_text(item.get("url")): item for item in reference_files if item.get("url")}
    urls = image_urls or list(by_url.keys())
    catalog: List[Dict[str, Any]] = []
    for idx, url in enumerate(urls, 1):
        asset = by_url.get(url, {})
        local = local_by_url.get(url, {})
        review_bits = []
        for key in ["role", "bucket", "text_density", "visual_review_bucket", "visual_review_reason", "reason"]:
            value = asset.get(key)
            if value:
                review_bits.append(f"{key}: {value}")
        face_risk = local_image_has_face(local.get("path", ""))
        person_risk = local_image_has_full_person(local.get("path", ""))
        catalog.append({
            "index": idx,
            "url": url,
            "local_path": local.get("path", ""),
            "role": asset.get("role", "visual_reference"),
            "bucket": asset.get("bucket") or asset.get("visual_review_bucket") or "unknown",
            "text_density": asset.get("text_density") or "unknown",
            "ark_face_risk": face_risk,
            "ark_person_risk": person_risk,
            "product_visible_assumption": asset.get("role") != "copy_or_usage_reference" and asset.get("text_density") not in {"high", "medium"},
            "review_summary": "; ".join(review_bits)[:500],
        })
    return catalog


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def compact_benchmark_reference_patterns(video_analysis_path: Path, max_patterns: int, *, min_confidence: float = 0.6, policy: str = "auto") -> List[Dict[str, Any]]:
    """Load benchmark MLLM output as compact optional shooting references.

    These references are inspiration for filming/editing methods only. Product
    claims still come from the customer's landing page brief.
    """
    data = json.loads(video_analysis_path.read_text(encoding="utf-8"))
    analyses = data.get("video_analyses") if isinstance(data.get("video_analyses"), list) else []
    patterns: List[Dict[str, Any]] = []
    for analysis in analyses:
        if not isinstance(analysis, dict) or analysis.get("error"):
            continue
        confidence = as_float(analysis.get("confidence"), 0.0)
        if policy != "force" and confidence and confidence < min_confidence:
            continue
        source = analysis.get("source") if isinstance(analysis.get("source"), dict) else {}
        first3 = analysis.get("first_3_seconds") if isinstance(analysis.get("first_3_seconds"), dict) else {}
        transferable = analysis.get("transferable_to_customer") if isinstance(analysis.get("transferable_to_customer"), list) else []
        visual_patterns = analysis.get("visual_patterns") if isinstance(analysis.get("visual_patterns"), list) else []
        copy_patterns = analysis.get("copy_patterns") if isinstance(analysis.get("copy_patterns"), list) else []
        if policy != "force" and not (transferable or visual_patterns or copy_patterns or first3):
            continue
        patterns.append({
            "index": len(patterns) + 1,
            "video_id": norm_text(analysis.get("video_id") or source.get("Video ID") or source.get("video_id")),
            "similarity_score": as_float(source.get("similarity_score"), 0.0),
            "ctr": source.get("CTR") or source.get("ctr"),
            "rank": source.get("rank") or source.get("Rank"),
            "external_url": source.get("External Website URL") or source.get("external_url"),
            "confidence": confidence,
            "first_3_seconds": {
                "hook_type": first3.get("hook_type"),
                "what_happens": first3.get("what_happens") or first3.get("summary"),
                "overlay_or_voiceover": first3.get("overlay_or_voiceover") or first3.get("overlay") or first3.get("voiceover"),
                "why_it_works": first3.get("why_it_works"),
            },
            "creative_structure": analysis.get("creative_structure") if isinstance(analysis.get("creative_structure"), list) else [],
            "visual_patterns": visual_patterns[:8],
            "copy_patterns": copy_patterns[:8],
            "transferable_to_customer": transferable[:8],
            "not_recommended_to_copy": (analysis.get("not_recommended_to_copy") if isinstance(analysis.get("not_recommended_to_copy"), list) else [])[:8],
        })
        if len(patterns) >= max(0, max_patterns):
            break
    return patterns


def load_benchmark_reference_patterns(args: argparse.Namespace, video_count: int) -> List[Dict[str, Any]]:
    if getattr(args, "reference_pattern_policy", "auto") == "off":
        return []
    path_text = norm_text(getattr(args, "benchmark_video_analysis", ""))
    if not path_text and norm_text(getattr(args, "benchmark_output_dir", "")):
        path_text = str(Path(args.benchmark_output_dir) / "video_analysis.json")
    if not path_text:
        return []
    path = Path(path_text)
    wait_for_parallel_benchmark_if_needed(args, path)
    if not path.exists():
        if getattr(args, "reference_pattern_policy", "auto") == "force":
            raise FileNotFoundError(f"benchmark video analysis not found: {path}")
        print(f"[benchmark-reference] skip missing video_analysis.json: {path}", flush=True)
        return []
    max_patterns = int(getattr(args, "max_reference_patterns", 0) or 0) or max(1, video_count)
    patterns = compact_benchmark_reference_patterns(
        path,
        max_patterns,
        min_confidence=float(getattr(args, "reference_pattern_min_confidence", 0.6) or 0.0),
        policy=getattr(args, "reference_pattern_policy", "auto"),
    )
    print(f"[benchmark-reference] loaded_patterns={len(patterns)} from {path}", flush=True)
    return patterns


def benchmark_pattern_by_index(patterns: List[Dict[str, Any]], raw_index: Any) -> Dict[str, Any]:
    try:
        idx = int(raw_index)
    except Exception:
        return {}
    for pattern in patterns:
        if int(pattern.get("index") or 0) == idx:
            return pattern
    return {}


def build_planner_prompt(brief: Dict[str, Any], reference_catalog: List[Dict[str, Any]], image_count: int, video_count: int, benchmark_reference_patterns: Optional[List[Dict[str, Any]]] = None, reference_pattern_policy: str = "auto") -> str:
    lang = caption_builder.normalize_language(brief.get("language", "en"))
    language_name = caption_builder.LANGUAGE_NAMES.get(lang, "English")
    planner_input = {
        "language": lang,
        "language_name": language_name,
        "brand": brief.get("brand"),
        "product": brief.get("product"),
        "product_category": brief.get("product_category"),
        "business_type": brief.get("business_type"),
        "conversion_action": brief.get("conversion_action"),
        "description": brief.get("description"),
        "target_audience_or_pain_points": brief.get("pain_points", []),
        "usage_scenarios": brief.get("usage_scenarios", []),
        "supported_claims": brief.get("supported_claims", []) or brief.get("selling_points", []),
        "selling_points": brief.get("selling_points", []),
        "creative_angles": brief.get("creative_angles", []),
        "image_text_sources": brief.get("image_text_sources", []),
        "reference_images": reference_catalog,
        "benchmark_reference_patterns": benchmark_reference_patterns or [],
        "reference_pattern_policy": reference_pattern_policy,
        "requested_counts": {"image_assets": image_count, "videos": video_count},
        "recommended_image_asset_styles": default_asset_style_specs(),
    }
    return (
        "You are the generation planner for a URL-to-TikTok-creative pipeline. Return strict JSON only, no markdown.\n"
        "Plan exactly the requested number of image assets and videos. Use only the indexed reference_images supplied below.\n"
        "Requirements:\n"
        f"- The landing page dominant language is {language_name}. Video hooks, overlays, and voiceover directions must use {language_name}. Do not default to English unless {language_name} is English.\n"
        "- If people appear, use a natural foreign/international creator look suitable for US/EU TikTok ads.\n"
        "- Do not invent unsupported product claims, certifications, discounts, reviews, ingredients, or results. Use supported_claims/selling_points/image_text_sources only for videos/caption planning.\n"
        "- For image assets, DO NOT describe product-specific visual details such as color, material, exact shape, packaging text, ingredient, logo layout, SKU details, or visible product features. The image model will freely invent those details. Refer to the item only as 'the product/item from the reference image', 'wearing the referenced item', 'holding the referenced item', 'using the referenced product', or 'lower body wearing the item from the reference image'.\n"
        "- Image prompt_direction should focus only on visual scene type: person type, pose, framing, camera angle, lighting, room/location, hands, body crop, action, and UGC vibe.\n"
        "- Do not split image assets by selling point. Image assets must be diversified by scene/person/action/framing only. Keep image_assets.selling_point empty by default. If you cannot visually ground a point from the selected reference, leave it empty.\n"
        "- Each image asset must name its creative type/style, prompt direction, and one or more reference image indices. At least one chosen reference should show the product; avoid text-heavy refs for generated visuals unless it is only for copy inspiration.\n"
        "- Use recommended_image_asset_styles as a style catalog and prefer those styles when suitable, but you may output another style if the references clearly call for it. Do not treat the catalog as a local draft plan.\n"
        "- The style field is chosen by you from the supplied references, brand context, and recommended style catalog. It must describe a visual scene type, not a product category or selling-point template.\n"
        "- Combination/grid/bundle image styles may appear at most 2 times across image_assets, and only when at least 2 uploaded references visibly provide those items. Use 2-4 reference_image_indices for them. Never invent extra colors, sizes, styles, bundle components, packaging, or variants that are not in the references.\n"
        "- Each video must choose a distinct angle, hook, primary supported selling point, reference image indices, and caption direction. Videos may overlap references but should cover different hooks/claims/scenarios.\n"
        "- benchmark_reference_patterns are optional high-performing reference-video shooting/editing methods from similar landing pages. Use them only when product/use-case fit is strong enough. Do not copy competitor assets, exact scripts, unsupported claims, offers, reviews, or category-mismatched tactics.\n"
        "- If benchmark_reference_patterns are useful, assign at most one benchmark_reference_pattern_index to each video and prefer distinct patterns across videos (for 3 videos, up to 3 distinct patterns). Adapt the reusable filming method, not the competitor's product claim.\n"
        "- If no benchmark pattern fits a video, set benchmark_reference_pattern_index to null and explain why in benchmark_reference_usage. Do not force a weak match unless reference_pattern_policy is force, and even then keep claims grounded in supported_claims.\n"
        "- Do not force first-frame/last-frame instructions. Images are references only.\n"
        "- Keep text overlays short; avoid dense text because image/video models handle text poorly.\n"
        "- Avoid meme/promotional-poster styles for serious, regulated, medical, or sensitive products unless clearly safe.\n"
        "Schema:\n"
        "{\n"
        "  \"planning_notes\": \"short rationale\",\n"
        "  \"image_assets\": [\n"
        "    {\"index\": 1, \"style\": \"ugc_lifestyle|routine_scene|creator_fit_check|hands_closeup|outdoor_street|variant_or_bundle_grid|collage_grid|other\", \"scene_type\": \"visual scene type, not a selling point\", \"reference_image_indices\": [1], \"product_use_phrase\": \"wearing/holding/using the referenced product, or lower body wearing the item from the reference image; for variant_or_bundle_grid: show only the referenced items together\", \"selling_point\": \"usually empty\", \"prompt_direction\": \"scene/person/action direction without product detail description\", \"avoid\": [\"dense text\", \"describing product color/material/shape from memory\"], \"reason\": \"why this ref and scene\"}\n"
        "  ],\n"
        "  \"videos\": [\n"
        "    {\"index\": 1, \"angle\": \"distinct video angle\", \"primary_selling_point\": \"supported point\", \"reference_image_indices\": [1,2,3], \"caption_direction\": \"what the 15s caption should emphasize\", \"hook\": \"short hook in landing-page language\", \"benchmark_reference_pattern_index\": 1|null, \"benchmark_reference_usage\": \"how to adapt the filming/editing method, or why not used\", \"reason\": \"why this plan\"}\n"
        "  ]\n"
        "}\n"
        "Input JSON:\n" + json.dumps(planner_input, ensure_ascii=False, indent=2)
    )


def call_modelhub_generation_planner(prompt: str, args: argparse.Namespace, run_dir: Path, case_id: str) -> Dict[str, Any]:
    endpoint = args.generation_planner_endpoint
    if not endpoint:
        raise RuntimeError("generation planner endpoint is empty")
    payload = {
        "stream": False,
        "model": args.generation_planner_model,
        "max_tokens": args.generation_planner_max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    headers = {"Content-Type": "application/json"}
    logid = args.generation_planner_logid or f"codex-generation-planner-{case_id}-{int(time.time())}"
    if logid:
        headers["X-TT-LOGID"] = logid
    dump_json(run_dir / f"{case_id}_generation_planner_request_redacted.json", {"endpoint": endpoint.split("ak=")[0] + "ak=[REDACTED]" if "ak=" in endpoint else endpoint, "payload": payload, "logid": logid})

    def validate(raw_response: Dict[str, Any]) -> None:
        content = extract_chat_content(raw_response)
        if not content:
            raise RuntimeError("generation planner returned empty content")
        if not parse_json_object_from_text(content):
            raise RuntimeError("generation planner returned no parseable JSON")

    raw = post_llm_json_with_sleep_retry(
        endpoint,
        headers,
        payload,
        args.generation_planner_timeout,
        attempts=llm_retry_attempts(args),
        sleep_seconds=llm_retry_sleep_seconds(args),
        validate=validate,
    )
    dump_json(run_dir / f"{case_id}_generation_planner_response.json", raw)
    content = extract_chat_content(raw)
    parsed = parse_json_object_from_text(content)
    if not parsed:
        raise RuntimeError("generation planner returned no parseable JSON")
    return parsed


def call_modelhub_caption_generator(prompt: str, brief_context: str, args: argparse.Namespace, run_dir: Path, case_id: str, suffix: str = "") -> Dict[str, Any]:
    """Generate the final Ark caption with ModelHub.

    `brief_context` is generated from the LLM caption brief and is used only as
    structured context. The returned caption must be strict JSON.
    """
    endpoint = args.caption_generator_endpoint or args.generation_planner_endpoint
    if not endpoint:
        raise RuntimeError("caption generator endpoint is empty")
    full_prompt = (
        prompt
        + "\n\nAdditional context serialized from the LLM caption brief. Use it as context only; "
        + "return the requested JSON object and keep all claims grounded in the structured input:\n"
        + brief_context
    )
    payload = {
        "stream": False,
        "model": args.caption_generator_model,
        "max_tokens": args.caption_generator_max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": full_prompt}]}],
    }
    headers = {"Content-Type": "application/json"}
    logid = args.caption_generator_logid or f"codex-caption-generator-{case_id}-{int(time.time())}"
    if logid:
        headers["X-TT-LOGID"] = logid
    file_suffix = f"_{suffix}" if suffix else ""
    dump_json(run_dir / f"{case_id}_caption_generator_request_redacted{file_suffix}.json", {"endpoint": endpoint.split("ak=")[0] + "ak=[REDACTED]" if "ak=" in endpoint else endpoint, "payload": payload, "logid": logid})

    def validate(raw_response: Dict[str, Any]) -> None:
        content = extract_chat_content(raw_response)
        if not content:
            raise RuntimeError("caption generator returned empty content")
        parsed_response = parse_json_object_from_text(content)
        if not norm_text(parsed_response.get("final_ark_caption") if parsed_response else ""):
            raise RuntimeError("caption generator returned no strict JSON final_ark_caption")

    raw = post_llm_json_with_sleep_retry(
        endpoint,
        headers,
        payload,
        args.caption_generator_timeout,
        attempts=llm_retry_attempts(args),
        sleep_seconds=llm_retry_sleep_seconds(args),
        validate=validate,
    )
    dump_json(run_dir / f"{case_id}_caption_generator_response{file_suffix}.json", raw)
    content = extract_chat_content(raw)
    parsed = parse_json_object_from_text(content)
    if parsed:
        final_caption = norm_text(parsed.get("final_ark_caption"))
        if final_caption:
            parsed["final_ark_caption"] = final_caption
            return parsed
    raise RuntimeError("caption generator returned no strict JSON final_ark_caption")


def build_caption_brief_prompt(brief_input: Dict[str, Any]) -> str:
    lang = caption_builder.normalize_language(brief_input.get("language", "en"))
    language_name = caption_builder.LANGUAGE_NAMES.get(lang, "English")
    return (
        "You are the creative strategist for a generalized URL-to-TikTok-ad pipeline. Return strict JSON only, no markdown.\n"
        "Create the complete caption brief from the raw structured landing-page data below. Do not use fixed category templates. "
        "The customer URL can be any product/service category, so infer only from the supplied evidence.\n"
        "Requirements:\n"
        f"- The landing page dominant language is {language_name}. All hook, overlay, scene captions, and voiceover fields must be in {language_name}. Do not default to English unless {language_name} is English.\n"
        "- Infer product_category as open text from evidence, not a fixed enum.\n"
        "- Generate TikTok-native UGC strategy: casual creator, realistic scene world, short overlays, fast but coherent 15s structure.\n"
        "- Every supported claim must come from selling_points, description, image_text_sources, or clean_web_sources. Do not invent claims, discounts, certifications, reviews, medical effects, or results.\n"
        "- Choose hook, title_overlays, creator_style, scene_world, and scene_plan by reasoning from the data. Do not copy generic category templates.\n"
        "- selected_images must reuse the input selected_images exactly by URL; do not fabricate image URLs.\n"
        "- Scene plan must cover exactly 15 seconds in 5 or 6 timestamps. Each scene needs camera, subject, action, setting, lighting, on_screen_caption, voiceover, audio, and visual.\n"
        "- Shot pacing constraint: in any rolling 3-second window, use at most 4 distinct shots/cuts/camera resets.\n"
        "- Visual action, on-screen caption, and voiceover must be semantically related in each scene.\n"
        "- Reference images are visual references only; do not force first frame, last frame, freeze frame, or exact recreation.\n"
        "- Keep overlay text short and safe-zone friendly; avoid dense text.\n"
        "Required JSON schema:\n"
        "{\n"
        "  \"case_id\": \"string\",\n"
        "  \"language\": \"language code\",\n"
        "  \"language_name\": \"string\",\n"
        "  \"brand\": \"string\",\n"
        "  \"product\": \"string\",\n"
        "  \"product_category\": \"open-text category inferred by LLM\",\n"
        "  \"business_type\": \"string\",\n"
        "  \"conversion_action\": \"string\",\n"
        "  \"description\": \"string\",\n"
        "  \"selling_points\": [\"supported point\"],\n"
        "  \"pain_points\": [\"supported/inferred pain point\"],\n"
        "  \"usage_scenarios\": [\"scenario\"],\n"
        "  \"creative_angles\": [{\"angle\": \"string\", \"hook\": \"string\", \"supporting_points\": [\"string\"], \"evidence\": [\"string\"]}],\n"
        "  \"supported_claims\": [\"claim\"],\n"
        "  \"image_text_sources\": [{\"url\": \"string\", \"visible_text\": \"string\", \"key_claims\": [\"string\"]}],\n"
        "  \"selected_images\": [{\"url\": \"must match input URL\", \"role\": \"string\", \"bucket\": \"string\", \"text_density\": \"string\"}],\n"
        "  \"creator_style\": {\"persona\": \"string\", \"scene_world\": \"string\", \"voice\": \"string\", \"visual_style\": [\"string\"]},\n"
        "  \"hook\": {\"caption\": \"short overlay\", \"voiceover\": \"spoken hook\", \"visual\": \"matching visual setup\"},\n"
        "  \"title_overlays\": [\"short overlay phrase\"],\n"
        "  \"scene_plan\": [{\"time\": \"0-2s\", \"label\": \"string\", \"shot_type\": \"string\", \"camera\": \"string\", \"subject\": \"string\", \"action\": \"string\", \"setting\": \"string\", \"lighting\": \"string\", \"on_screen_caption\": \"short caption\", \"voiceover\": \"spoken line\", \"audio\": \"SFX/music\", \"visual\": \"combined director visual\"}]\n"
        "}\n"
        "Raw structured input JSON:\n" + json.dumps(brief_input, ensure_ascii=False, indent=2)
    )


def normalize_caption_brief(raw: Dict[str, Any], brief_input: Dict[str, Any]) -> Dict[str, Any]:
    required_str = ["brand", "product", "product_category", "description"]
    brief = dict(raw)
    brief["case_id"] = norm_text(brief.get("case_id") or brief_input.get("case_id"))
    brief["language"] = caption_builder.normalize_language(brief.get("language") or brief_input.get("language", "en"))
    brief["language_name"] = caption_builder.LANGUAGE_NAMES.get(brief["language"], "English")
    for key in required_str:
        if not norm_text(brief.get(key)):
            raise RuntimeError(f"caption brief missing required field: {key}")
        brief[key] = norm_text(brief.get(key))
    for key in ["selling_points", "pain_points", "usage_scenarios", "supported_claims", "title_overlays"]:
        value = brief.get(key)
        if not isinstance(value, list):
            raise RuntimeError(f"caption brief field must be list: {key}")
    if "image_text_sources" in brief and not isinstance(brief.get("image_text_sources"), list):
        raise RuntimeError("caption brief field must be list: image_text_sources")
    if "image_text_sources" not in brief:
        # Preserve crawler/vision evidence for traceability; all creative fields
        # above are still required from the LLM response.
        brief["image_text_sources"] = brief_input.get("image_text_sources") if isinstance(brief_input.get("image_text_sources"), list) else []
    for key in ["creative_angles", "scene_plan", "selected_images"]:
        value = brief.get(key)
        if not isinstance(value, list):
            raise RuntimeError(f"caption brief field must be list: {key}")
    style = brief.get("creator_style")
    if not isinstance(style, dict) or not norm_text(style.get("persona")) or not norm_text(style.get("scene_world")) or not norm_text(style.get("voice")):
        raise RuntimeError("caption brief missing creator_style persona/scene_world/voice")
    if not isinstance(style.get("visual_style"), list) or not style.get("visual_style"):
        raise RuntimeError("caption brief creator_style.visual_style must be a non-empty list")
    hook = brief.get("hook")
    if not isinstance(hook, dict) or not norm_text(hook.get("caption")) or not norm_text(hook.get("voiceover")):
        raise RuntimeError("caption brief missing hook caption/voiceover")
    input_assets = brief_input.get("selected_images") if isinstance(brief_input.get("selected_images"), list) else []
    allowed_urls = {norm_text(a.get("url")) for a in input_assets if isinstance(a, dict)}
    if not brief.get("selected_images"):
        raise RuntimeError("caption brief selected_images is empty")
    normalized_assets: List[Dict[str, Any]] = []
    for asset in brief.get("selected_images", []):
        if not isinstance(asset, dict):
            continue
        url = norm_text(asset.get("url"))
        if url not in allowed_urls:
            raise RuntimeError(f"caption brief selected image URL not in input: {url[:160]}")
        source = next((a for a in input_assets if norm_text(a.get("url")) == url), {})
        normalized_assets.append({**source, **asset, "url": url})
    brief["selected_images"] = normalized_assets
    if len(brief.get("scene_plan", [])) < 4:
        raise RuntimeError("caption brief scene_plan must contain at least 4 scenes")
    for idx, scene in enumerate(brief.get("scene_plan", []), 1):
        if not isinstance(scene, dict):
            raise RuntimeError(f"caption brief scene {idx} must be an object")
        for key in ["time", "on_screen_caption", "voiceover", "visual"]:
            if not norm_text(scene.get(key)):
                raise RuntimeError(f"caption brief scene {idx} missing {key}")
    return brief


def call_modelhub_caption_brief(prompt: str, args: argparse.Namespace, run_dir: Path, case_id: str, brief_input: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = args.caption_brief_endpoint or args.caption_generator_endpoint or args.generation_planner_endpoint
    if not endpoint:
        raise RuntimeError("caption brief endpoint is empty")
    payload = {
        "stream": False,
        "model": args.caption_brief_model,
        "max_tokens": args.caption_brief_max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    headers = {"Content-Type": "application/json"}
    logid = args.caption_brief_logid or f"codex-caption-brief-{case_id}-{int(time.time())}"
    if logid:
        headers["X-TT-LOGID"] = logid
    dump_json(run_dir / f"{case_id}_caption_brief_request_redacted.json", {"endpoint": endpoint.split("ak=")[0] + "ak=[REDACTED]" if "ak=" in endpoint else endpoint, "payload": payload, "logid": logid})

    def validate(raw_response: Dict[str, Any]) -> None:
        content = extract_chat_content(raw_response)
        if not content:
            raise RuntimeError("caption brief generator returned empty content")
        parsed_response = parse_json_object_from_text(content)
        if not parsed_response:
            raise RuntimeError("caption brief generator returned no parseable JSON")
        normalize_caption_brief(parsed_response, brief_input)

    raw_response = post_llm_json_with_sleep_retry(
        endpoint,
        headers,
        payload,
        args.caption_brief_timeout,
        attempts=llm_retry_attempts(args),
        sleep_seconds=llm_retry_sleep_seconds(args),
        validate=validate,
    )
    dump_json(run_dir / f"{case_id}_caption_brief_response.json", raw_response)
    content = extract_chat_content(raw_response)
    parsed = parse_json_object_from_text(content)
    if not parsed:
        raise RuntimeError("caption brief generator returned no parseable JSON")
    parsed["_modelhub_raw_content_preview"] = content[:1000]
    return normalize_caption_brief(parsed, brief_input)


def build_variant_caption_prompt(brief: Dict[str, Any], plan_item: Dict[str, Any]) -> str:
    """Build a per-video LLM prompt so each Ark task gets its own full script.

    The base caption is still written for review, but video execution should not
    rely on local string appending. This prompt asks the LLM to produce a
    complete 15s director script for the specific variant plan.
    """
    lang = caption_builder.normalize_language(brief.get("language", "en"))
    language_name = caption_builder.LANGUAGE_NAMES.get(lang, "English")
    selected_refs = plan_item.get("selected_reference_images") or []
    prompt_input = {
        "brief": brief,
        "video_variant_plan": {
            "index": plan_item.get("index"),
            "angle": plan_item.get("angle"),
            "primary_selling_point": plan_item.get("primary_selling_point"),
            "hook": plan_item.get("hook"),
            "caption_direction": plan_item.get("caption_direction"),
            "reason": plan_item.get("reason"),
            "reference_image_indices": plan_item.get("reference_image_indices"),
            "reference_image_urls": plan_item.get("reference_image_urls"),
            "selected_reference_images": selected_refs,
            "benchmark_reference_pattern_index": plan_item.get("benchmark_reference_pattern_index"),
            "benchmark_reference_pattern": plan_item.get("benchmark_reference_pattern") or {},
            "benchmark_reference_usage": plan_item.get("benchmark_reference_usage") or "",
        },
    }
    return (
        "You are generating ONE final Ark image-to-video caption for a specific TikTok ad variant. "
        "Return strict JSON only, no markdown.\n"
        f"The landing page dominant language is {language_name}. All on-screen captions and voiceover must be in {language_name}. "
        f"Do not default to English unless {language_name} is English.\n"
        "Write a polished 15-second vertical 9:16 TikTok Non-Spark UGC ad caption. "
        "The uploaded images are references only: product appearance, usage cues, color/style, packaging consistency. "
        "Do not force first frame, last frame, freeze frame, or exact image recreation.\n"
        "The variant must follow video_variant_plan. Each timestamp must include camera, subject/action, setting, on-screen caption, voiceover, and SFX/audio. "
        "Visual action, on-screen caption, and voiceover must describe the same moment. Use concrete TikTok shot language: handheld close-up, POV, mirror selfie, snap zoom, whip-pan, rack focus, jump cut, macro detail, tracking move. "
        "Shot pacing constraint: in any rolling 3-second window, use at most 4 distinct shots/cuts/camera resets.\n"
        "If video_variant_plan includes benchmark_reference_pattern, use it only as optional inspiration for filming method, hook structure, pacing, camera moves, or edit rhythm. Do not copy the competitor's exact script, product claims, offer, reviews, or visuals. If the pattern feels mismatched, ignore it and keep the variant grounded in the customer's brief.\n"
        "Use a natural foreign/international creator look when people appear. Keep it native TikTok, not polished TVC. "
        "Keep text overlays short and safe-zone aware. Avoid dense text. Avoid medical claims, fake reviews, invented discounts, certifications, competitor mentions, and unsupported results.\n"
        "Return JSON schema:\n"
        "{\n"
        "  \"variant_index\": 1,\n"
        "  \"selected_images\": [{\"url\": \"...\", \"caption_reference_name\": \"Image 1\", \"role\": \"...\"}],\n"
        "  \"title_overlays\": [\"short overlay\"],\n"
        "  \"voiceover_lines\": [\"line\"],\n"
        "  \"final_ark_caption\": \"complete Ark caption text\"\n"
        "}\n"
        "Input JSON:\n" + json.dumps(prompt_input, ensure_ascii=False, indent=2)
    )


def generate_variant_caption_with_llm(
    brief: Dict[str, Any],
    plan_item: Dict[str, Any],
    base_caption: str,
    args: argparse.Namespace,
    job_dir: Path,
    case_id: str,
) -> Dict[str, Any]:
    prompt = build_variant_caption_prompt(brief, plan_item)
    suffix = f"video_{int(plan_item.get('index', 0)):02d}"
    (job_dir / f"{suffix}_caption_prompt.txt").write_text(prompt, encoding="utf-8")
    result = call_modelhub_caption_generator(prompt, base_caption, args, job_dir, case_id, suffix=suffix)
    caption = norm_text(result.get("final_ark_caption"))
    if not caption:
        raise RuntimeError("empty final_ark_caption")
    result.update({"generator": "modelhub", "status": "modelhub_completed", "final_ark_caption": caption})
    dump_json(job_dir / f"{suffix}_caption_generator_result.json", result)
    return result


def coerce_indices(raw_indices: Any, max_index: int, limit: int = 1) -> List[int]:
    indices: List[int] = []
    if isinstance(raw_indices, list):
        for value in raw_indices:
            try:
                idx = int(value)
            except Exception:
                continue
            if 1 <= idx <= max_index:
                indices.append(idx)
    indices = [int(value) for value in uniq_keep_order([str(i) for i in indices])]
    return indices[: max(1, limit)] if max_index > 0 else []


def replace_selected_refs_in_caption(base_caption: str, selected_refs: List[Dict[str, Any]]) -> str:
    """Rewrite the reference-image section so each video variant names only its planned refs."""
    if not selected_refs:
        return base_caption
    image_lines = []
    for i, ref in enumerate(selected_refs, 1):
        role = ref.get("role") or "visual_reference"
        bucket = ref.get("bucket") or "unknown"
        image_lines.append(f"Image {i}: use as {role} ({bucket}) for product appearance, packaging consistency, usage cues, or visual style. Do not copy it as an exact shot.")
    replacement = "Reference image usage:\n" + "\n".join(image_lines) + "\n\nTimeline. Keep each shot visually matched to the voiceover:"
    pattern = r"Reference image usage:\n.*?\n\nTimeline\. Keep each shot visually matched to the voiceover:"
    updated = re.sub(pattern, replacement, base_caption, flags=re.S)
    return updated if updated != base_caption else base_caption + "\n\nVariant reference images:\n" + "\n".join(image_lines)


def image_prompt_from_planner_item(brief: Dict[str, Any], item: Dict[str, Any]) -> str:
    style = norm_text(item.get("style"))
    scene_type = norm_text(item.get("scene_type"))
    prompt_direction = norm_text(item.get("prompt_direction"))
    product_use_phrase = norm_text(item.get("product_use_phrase"))
    if not style or not scene_type or not prompt_direction or not product_use_phrase:
        raise RuntimeError("generation planner image item missing style/scene_type/prompt_direction/product_use_phrase")
    avoid = item.get("avoid") if isinstance(item.get("avoid"), list) else []
    avoid_items = [norm_text(x) for x in avoid if norm_text(x)]
    avoid_items.extend([
        "describing or changing product color/material/shape/logo/package text",
        "describing product-specific visual details from memory",
        "turning the prompt into a feature or benefit diagram",
        "inventing product features or variants",
        "dense text overlays",
        "fake claims, fake discounts, fake reviews, extra logos",
    ])
    avoid_text = ", ".join(uniq_keep_order(avoid_items))
    combination_extra = ""
    if is_combination_image_style(style):
        combination_extra = (
            " This is a controlled combination/collection image. Use all uploaded reference images as source items. "
            "Show only the items visible in those reference images together in a clean TikTok-native collection, flat-lay, closet grid, bundle unboxing, or simple multi-item layout. "
            "Do not create additional colors, product variants, sizes, bundle components, packaging, labels, logos, or product details beyond the uploaded references. "
            "If the references do not actually show multiple variants or bundle items, make a simple collage of the referenced product scenes instead of inventing a collection."
        )
    return (
        "Create one TikTok-native ad image. The uploaded reference image(s) are the only source of truth for the product appearance. "
        "Do not describe, redesign, recolor, re-texture, relabel, or reinterpret the product. Do not add new product variants, labels, logos, patterns, ingredients, materials, shapes, or colors. "
        "Refer to the product only with neutral reference-image phrases such as the referenced product/item, wearing the referenced item, holding the referenced item, using the referenced product, or lower body wearing the item from the reference image. "
        f"Product visibility requirement: {product_use_phrase}. "
        f"Visual scene type: {scene_type}. Scene/person/action direction: {prompt_direction}. "
        f"{combination_extra} "
        "Do not make the image concept depend on a selling point. If a benefit is not directly obvious from the reference image, ignore it for image generation. "
        "Focus creative freedom on the person, pose, camera angle, crop, lighting, room/location, props, expression, motion, and realistic UGC phone-shot mood, not on product details. "
        "Default market style: English-language TikTok ad creative for an overseas audience. If a person appears, use a natural foreign/international creator image suitable for US/EU TikTok ads. "
        f"Avoid: {avoid_text}. Prefer no text. If text appears, keep it extremely short, readable, and in English only."
    )


def normalize_planner_output(
    planner_output: Dict[str, Any],
    brief: Dict[str, Any],
    reference_catalog: List[Dict[str, Any]],
    image_count: int,
    video_count: int,
    benchmark_reference_patterns: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    max_index = len(reference_catalog)
    if (image_count or video_count) and max_index == 0:
        raise RuntimeError("generation planner requires at least one selected reference image")
    refs_by_index = {item["index"]: item for item in reference_catalog}
    raw_images = planner_output.get("image_assets") if isinstance(planner_output.get("image_assets"), list) else []
    raw_videos = planner_output.get("videos") if isinstance(planner_output.get("videos"), list) else []
    if len(raw_images) < image_count:
        raise RuntimeError(f"generation planner returned {len(raw_images)} image_assets, expected {image_count}")
    if len(raw_videos) < video_count:
        raise RuntimeError(f"generation planner returned {len(raw_videos)} videos, expected {video_count}")

    image_plan: List[Dict[str, Any]] = []
    combination_count = 0
    for idx in range(image_count):
        raw = raw_images[idx]
        if not isinstance(raw, dict):
            raise RuntimeError(f"generation planner image_assets[{idx}] must be an object")
        style = norm_text(raw.get("style"))
        if not style:
            raise RuntimeError(f"generation planner image_assets[{idx}] missing style")
        if is_combination_image_style(style):
            if combination_count >= 2 or max_index < 2:
                raise RuntimeError("generation planner produced too many combination/grid image assets or not enough references")
            else:
                combination_count += 1
        ref_limit = 4 if is_combination_image_style(style) else 2
        chosen_indices = coerce_indices(raw.get("reference_image_indices"), max_index, limit=ref_limit)
        if not chosen_indices:
            raise RuntimeError(f"generation planner image_assets[{idx}] missing valid reference_image_indices")
        if is_combination_image_style(style) and len(chosen_indices) < 2:
            raise RuntimeError(f"generation planner image_assets[{idx}] combination style requires at least 2 references")
        chosen_refs = [refs_by_index[i] for i in chosen_indices if i in refs_by_index]
        scene_type = norm_text(raw.get("scene_type"))
        intent = norm_text(raw.get("prompt_direction"))
        product_use_phrase = norm_text(raw.get("product_use_phrase"))
        if not scene_type or not intent or not product_use_phrase:
            raise RuntimeError(f"generation planner image_assets[{idx}] missing scene_type/prompt_direction/product_use_phrase")
        selling_point = norm_text(raw.get("selling_point") or "")
        item = {
            "index": idx + 1,
            "style": style,
            "scene_type": scene_type,
            "intent": intent,
            "reference_image_indices": chosen_indices,
            "reference_image_paths": [r.get("local_path", "") for r in chosen_refs if r.get("local_path")],
            "reference_image_urls": [r.get("url", "") for r in chosen_refs if r.get("url")],
            # Backward compatibility for old one-reference executor.
            "reference_image_path": next((r.get("local_path", "") for r in chosen_refs if r.get("local_path")), ""),
            "reference_image_url": next((r.get("url", "") for r in chosen_refs if r.get("url")), ""),
            "selling_point": selling_point,
            "product_use_phrase": product_use_phrase,
            "prompt_direction": norm_text(raw.get("prompt_direction") or intent),
            "avoid": raw.get("avoid") if isinstance(raw.get("avoid"), list) else [],
            "reason": norm_text(raw.get("reason")),
        }
        item["prompt"] = image_prompt_from_planner_item(brief, item)
        image_plan.append(item)

    video_plan: List[Dict[str, Any]] = []
    for idx in range(video_count):
        raw = raw_videos[idx]
        if not isinstance(raw, dict):
            raise RuntimeError(f"generation planner videos[{idx}] must be an object")
        chosen_indices = coerce_indices(raw.get("reference_image_indices"), max_index, limit=3)
        if not chosen_indices:
            raise RuntimeError(f"generation planner videos[{idx}] missing valid reference_image_indices")
        chosen_refs = [refs_by_index[i] for i in chosen_indices if i in refs_by_index]
        ark_safe_refs = [r for r in chosen_refs if not (r.get("ark_face_risk") or r.get("ark_person_risk"))]
        ark_unsafe_refs = [r for r in chosen_refs if (r.get("ark_face_risk") or r.get("ark_person_risk"))]
        if not ark_safe_refs:
            replacement_refs = [r for r in reference_catalog if not (r.get("ark_face_risk") or r.get("ark_person_risk")) and r.get("url")]
            if replacement_refs:
                ark_safe_refs = replacement_refs[: min(2, len(replacement_refs))]
        angle = norm_text(raw.get("angle"))
        primary = norm_text(raw.get("primary_selling_point"))
        caption_direction = norm_text(raw.get("caption_direction"))
        if not angle or not primary or not caption_direction:
            raise RuntimeError(f"generation planner videos[{idx}] missing angle/primary_selling_point/caption_direction")
        raw_pattern_index = raw.get("benchmark_reference_pattern_index")
        selected_pattern = benchmark_pattern_by_index(benchmark_reference_patterns or [], raw_pattern_index)
        pattern_index = selected_pattern.get("index") if selected_pattern else None
        video_plan.append({
            "index": idx + 1,
            "angle": angle,
            "primary_selling_point": primary,
            "reference_image_indices": chosen_indices,
            "selected_reference_images": chosen_refs,
            "reference_image_urls": [r.get("url", "") for r in chosen_refs if r.get("url")],
            "ark_safe_reference_image_urls": [r.get("url", "") for r in ark_safe_refs if r.get("url")],
            "ark_excluded_reference_images": ark_unsafe_refs,
            "caption_direction": caption_direction,
            "hook": norm_text(raw.get("hook") or ""),
            "benchmark_reference_pattern_index": pattern_index,
            "benchmark_reference_pattern": selected_pattern,
            "benchmark_reference_usage": norm_text(raw.get("benchmark_reference_usage") or ("No benchmark reference pattern selected." if not selected_pattern else "")),
            "reason": norm_text(raw.get("reason") or ""),
        })
    return {
        "planning_notes": norm_text(planner_output.get("planning_notes") or ""),
        "image_assets": image_plan,
        "videos": video_plan,
    }


def build_generation_plan(args: argparse.Namespace, run_dir: Path, case_id: str, brief: Dict[str, Any], image_urls: List[str], reference_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    reference_catalog = compact_reference_catalog(brief, reference_files, image_urls)
    image_count = args.image_asset_count if args.generate_image_assets else 0
    video_count = args.video_count if args.submit_ark else 0
    if args.generation_planner != "modelhub":
        raise RuntimeError("generation_planner must be modelhub; local planner generation has been removed")
    benchmark_reference_patterns = load_benchmark_reference_patterns(args, video_count)
    if benchmark_reference_patterns:
        dump_json(run_dir / f"{case_id}_benchmark_reference_patterns.json", benchmark_reference_patterns)
    prompt = build_planner_prompt(brief, reference_catalog, image_count, video_count, benchmark_reference_patterns, args.reference_pattern_policy)
    (run_dir / f"{case_id}_generation_planner_prompt.txt").write_text(prompt, encoding="utf-8")
    raw_output = call_modelhub_generation_planner(prompt, args, run_dir, case_id)
    normalized = normalize_planner_output(raw_output, brief, reference_catalog, image_count, video_count, benchmark_reference_patterns)
    normalized.update({
        "planner": "modelhub",
        "planner_status": "modelhub_completed",
        "raw_planner_output": raw_output,
        "reference_catalog": reference_catalog,
        "benchmark_reference_patterns": benchmark_reference_patterns,
        "reference_pattern_policy": args.reference_pattern_policy,
        "deadline_seconds": args.generation_max_wait_seconds,
    })
    return normalized



def run_image_asset_job(plan_item: Dict[str, Any], args: argparse.Namespace, out_dir: Path) -> Dict[str, Any]:
    job_dir = out_dir / f"image_asset_{plan_item['index']:02d}_{plan_item['style']}"
    ref_paths = plan_item.get("reference_image_paths") if isinstance(plan_item.get("reference_image_paths"), list) else []
    if not ref_paths and plan_item.get("reference_image_path"):
        ref_paths = [plan_item.get("reference_image_path")]
    valid_ref_paths: List[Path] = []
    for ref_path in ref_paths[:3]:
        if ref_path and Path(ref_path).exists():
            valid_ref_paths.append(Path(ref_path))
    if not valid_ref_paths:
        raise RuntimeError(
            "Refusing image generation without a valid local reference image. "
            "The product reference is required to avoid text-only hallucination."
        )
    effective_endpoint = image_asset_generator.endpoint_for_reference_mode(args.image_gen_endpoint, args.image_gen_reference_mode)
    prompt = plan_item["prompt"]
    payload: Dict[str, Any] = image_asset_generator.build_reference_payload(
        args.image_gen_model,
        prompt,
        valid_ref_paths,
        args.image_gen_size,
        args.image_gen_quality,
        1,
        args.image_gen_reference_mode,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    dump_json(job_dir / "plan.json", plan_item)
    base_url = args.image_gen_base_url or image_asset_generator.base_url_from_endpoint(effective_endpoint)
    (job_dir / "request_redacted.json").write_text(json.dumps({"endpoint": effective_endpoint.split("ak=")[0] + "ak=[REDACTED]" if "ak=" in effective_endpoint else effective_endpoint, "base_url": base_url, "reference_mode": args.image_gen_reference_mode, "payload": image_asset_generator.redact_payload(payload)}, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.image_gen_reference_mode == "sdk_edit":
        result = image_asset_generator.generate_edit_with_retry(
            base_url,
            args.image_gen_ak,
            args.image_gen_model,
            prompt,
            valid_ref_paths,
            args.image_gen_size,
            args.image_gen_quality,
            1,
            f"codex-image-asset-{int(time.time())}-{plan_item['index']}",
            0,
            args.generation_retry_sleep_seconds,
            args.image_gen_timeout,
            job_dir,
            max_seconds=args.generation_max_wait_seconds,
        )
    else:
        result = image_asset_generator.generate_with_retry(
            effective_endpoint,
            args.image_gen_ak,
            payload,
            f"codex-image-asset-{int(time.time())}-{plan_item['index']}",
            0,
            args.generation_retry_sleep_seconds,
            args.image_gen_timeout,
            job_dir,
            max_seconds=args.generation_max_wait_seconds,
        )
    dump_json(job_dir / "result.json", result)
    public_uploads: List[Dict[str, Any]] = []
    if result.get("ok"):
        for saved_path in result.get("saved_paths", []) or []:
            public_uploads.append(upload_file_to_public_url(Path(saved_path), args, asset_type="generated_image"))
        dump_json(job_dir / "public_uploads.json", public_uploads)
    return {
        "plan": plan_item,
        "ok": result.get("ok"),
        "output_dir": str(job_dir),
        "saved_paths": result.get("saved_paths", []),
        "public_uploads": public_uploads,
        "public_urls": [item.get("public_url") for item in public_uploads if item.get("ok") and item.get("public_url")],
        "error": result.get("error"),
        "attempt_count": len(result.get("attempts", [])),
    }


def is_non_retryable_ark_create_error(exc: Exception) -> bool:
    """Return True when retrying the same Ark key/payload cannot help."""
    status = getattr(exc, "status_code", None)
    phase = getattr(exc, "phase", "")
    if phase == "create" and status in {401, 403, 404}:
        return True
    if phase == "create" and status == 400 and is_ark_input_sensitive_error(exc):
        return True
    text = str(exc)
    return "Ark create failed: HTTP 403" in text or "AccessDenied" in text


def ark_reference_urls_for_attempt(plan_item: Dict[str, Any], use_safe_refs: bool) -> List[str]:
    if use_safe_refs:
        safe = [norm_text(u) for u in plan_item.get("ark_safe_reference_image_urls", []) if norm_text(u)]
        if safe:
            return safe
    return [norm_text(u) for u in plan_item.get("reference_image_urls", []) if norm_text(u)]


# Studio packshot instruction used when every reference image contains a real
# person and Ark image-to-video rejects them as sensitive/privacy content. The
# IMAGE_GEN edit service (unlike Ark) accepts person photos, so we ask it to
# re-render the same garment/product on a clean white background with no human.
WHITE_BG_PRODUCT_PROMPT = (
    "Convert the referenced item into a clean studio e-commerce packshot. "
    "Show ONLY the exact same product from the reference image - identical color, fabric, "
    "pattern, cut, print, and details - on a pure white seamless background. "
    "Completely remove any person, face, hands, skin, hair, and body parts: no human and "
    "no visible mannequin skin. Render it as a flat-lay or invisible/ghost-mannequin product "
    "photo. Centered composition, soft even studio lighting, no overlaid text or logos, no extra "
    "props. Do not invent colors, variants, sizes, or items that are not in the reference image."
)


def _resolve_local_ref_images_for_white_bg(plan_item: Dict[str, Any], job_dir: Path, args: argparse.Namespace, limit: int) -> List[Path]:
    """Collect up to ``limit`` local copies of the person reference images.

    Prefers already-downloaded local paths; falls back to downloading the public
    reference URLs so the IMAGE_GEN edit call always has real bytes to work from.
    """
    paths: List[Path] = []
    seen: set[str] = set()

    def add_local(raw: Any) -> None:
        lp = norm_text(raw)
        if lp and lp not in seen and Path(lp).exists():
            seen.add(lp)
            paths.append(Path(lp))

    for ref in plan_item.get("selected_reference_images") or []:
        if isinstance(ref, dict):
            add_local(ref.get("local_path"))
    for lp in plan_item.get("reference_image_paths") or []:
        add_local(lp)

    if not paths:
        urls = [norm_text(u) for u in (plan_item.get("reference_image_urls") or []) if norm_text(u)]
        dl_dir = job_dir / "white_bg_src"
        dl_dir.mkdir(parents=True, exist_ok=True)
        for i, url in enumerate(urls[:limit], 1):
            try:
                resp = requests.get(url, timeout=min(60, args.ark_timeout))
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").lower()
                ext = ".png" if "png" in ctype else (".webp" if "webp" in ctype else ".jpg")
                dest = dl_dir / f"src_{i:02d}{ext}"
                dest.write_bytes(resp.content)
                if dest.exists():
                    paths.append(dest)
            except Exception as exc:  # noqa: BLE001 - best-effort source fetch.
                print(f"[white-bg] could not download reference {url[:80]}: {exc}", flush=True)
    return paths[:limit]


def convert_video_refs_to_white_bg(plan_item: Dict[str, Any], args: argparse.Namespace, job_dir: Path) -> List[str]:
    """Re-render person reference images as white-background product packshots.

    Returns Ark-usable reference URLs (public CDN URLs when public upload is
    configured, otherwise inline base64 ``data:`` URLs). Empty list means the
    conversion was not possible; the caller then continues its normal handling.
    """
    if not norm_text(getattr(args, "image_gen_ak", "")) and "ak=" not in norm_text(getattr(args, "image_gen_endpoint", "")):
        print("[white-bg] IMAGE_GEN_AK not configured; cannot convert person refs to a white-bg product image.", flush=True)
        return []
    limit = max(1, int(getattr(args, "white_bg_max_refs", 1) or 1))
    src_paths = _resolve_local_ref_images_for_white_bg(plan_item, job_dir, args, limit)
    if not src_paths:
        print("[white-bg] no local or downloadable reference image available to convert.", flush=True)
        return []
    conv_dir = job_dir / "white_bg_product"
    conv_dir.mkdir(parents=True, exist_ok=True)
    effective_endpoint = image_asset_generator.endpoint_for_reference_mode(args.image_gen_endpoint, "sdk_edit")
    base_url = args.image_gen_base_url or image_asset_generator.base_url_from_endpoint(effective_endpoint)
    print(f"[white-bg] converting {len(src_paths)} person reference image(s) -> white-background product packshot for video_{plan_item['index']:02d}", flush=True)
    result = image_asset_generator.generate_edit_with_retry(
        base_url,
        args.image_gen_ak,
        args.image_gen_model,
        WHITE_BG_PRODUCT_PROMPT,
        src_paths,
        args.image_gen_size,
        args.image_gen_quality,
        1,
        f"white-bg-{int(time.time())}-{plan_item['index']}",
        3,
        args.generation_retry_sleep_seconds,
        args.image_gen_timeout,
        conv_dir,
        max_seconds=min(600, args.generation_max_wait_seconds),
    )
    dump_json(conv_dir / "white_bg_result_redacted.json", {
        "ok": result.get("ok"),
        "error": result.get("error"),
        "saved_paths": result.get("saved_paths", []),
        "attempt_count": len(result.get("attempts", []) or []),
    })
    if not result.get("ok"):
        print(f"[white-bg] conversion failed: {result.get('error')}", flush=True)
        return []
    ark_urls: List[str] = []
    for saved_path in (result.get("saved_paths") or [])[:limit]:
        p = Path(saved_path)
        if not p.exists():
            continue
        upload = upload_file_to_public_url(p, args, asset_type="white_bg_product_ref")
        if upload.get("ok") and upload.get("public_url"):
            ark_urls.append(upload["public_url"])
            host = "public_url"
        else:
            ark_urls.append(image_asset_generator.read_image_as_data_url(p))
            host = "inline_data_url"
        print(f"[white-bg] white-bg product reference ready via {host}: {p.name}", flush=True)
    return ark_urls


def run_ark_video_job(plan_item: Dict[str, Any], ark_targets: List[Dict[str, str]], base_caption: str, brief: Dict[str, Any], args: argparse.Namespace, out_dir: Path, case_id: str) -> Dict[str, Any]:
    job_dir = out_dir / f"video_{plan_item['index']:02d}"
    job_dir.mkdir(parents=True, exist_ok=True)
    if not ark_targets:
        raise RuntimeError("At least one Ark API key/model target is required for video generation")
    if args.video_caption_generator == "modelhub":
        caption_result = generate_variant_caption_with_llm(brief, plan_item, base_caption, args, job_dir, case_id)
        caption = norm_text(caption_result.get("final_ark_caption"))
        if not caption:
            raise RuntimeError("video caption generator returned empty final_ark_caption")
    else:
        raise RuntimeError("video_caption_generator must be modelhub; local caption generation has been removed")
    (job_dir / f"video_{plan_item['index']:02d}_ark_caption.txt").write_text(caption, encoding="utf-8")
    start_offset = int(plan_item.get("index", 1) - 1) % len(ark_targets)
    target_order = ark_targets[start_offset:] + ark_targets[:start_offset]
    redacted_targets = [{"api_key": redact_key(t["api_key"]), "model": t["model"]} for t in target_order]
    initial_reference_urls = ark_reference_urls_for_attempt(plan_item, use_safe_refs=True)
    preview_payload = ark_client.build_payload(
        caption,
        initial_reference_urls,
        model=target_order[0]["model"],
        duration=args.duration,
        ratio=args.ratio,
        generate_audio=not args.no_generate_audio,
    )
    dump_json(job_dir / "plan.json", {**plan_item, "ark_targets": redacted_targets, "caption_generator": caption_result})
    dump_json(job_dir / "ark_request_redacted.json", {"endpoint": args.ark_endpoint, "ark_targets": redacted_targets, "payload_for_first_target": preview_payload})
    deadline = time.perf_counter() + args.generation_max_wait_seconds
    attempts: List[Dict[str, Any]] = []
    attempt = 0
    last_error = ""
    disabled_keys: set[str] = set()
    force_safe_refs = True
    reference_urls = initial_reference_urls
    white_bg_attempted = False
    while time.perf_counter() < deadline:
        available_targets = [target for target in target_order if target["api_key"] not in disabled_keys]
        if not available_targets:
            last_error = "No Ark API keys left after non-retryable create failures"
            break
        target = available_targets[0]
        api_key = target["api_key"]
        model_name = target["model"]
        attempt += 1
        try:
            payload = ark_client.build_payload(
                caption,
                reference_urls,
                model=model_name,
                duration=args.duration,
                ratio=args.ratio,
                generate_audio=not args.no_generate_audio,
            )
            create_response = ark_client.submit_task(payload, endpoint=args.ark_endpoint, api_key=api_key, timeout=args.ark_timeout)
            dump_json(job_dir / f"ark_create_response_attempt_{attempt:02d}.json", create_response)
            task_id = ark_client.extract_task_id(create_response)
            if not task_id:
                raise RuntimeError(f"No Ark task id in create response: {create_response}")
            remaining = max(1, int(deadline - time.perf_counter()))
            poll_sleep = args.ark_poll_interval_seconds
            final_response = poll_ark_until_deadline(task_id, args.ark_endpoint, api_key, job_dir, f"video_{plan_item['index']:02d}", deadline, poll_sleep, args.ark_timeout)
            dump_json(job_dir / "ark_final_response.json", final_response)
            out_video = job_dir / f"video_{plan_item['index']:02d}_{args.duration}s.mp4"
            source_url = ark_client.download_first_video(final_response, out_video)
            if source_url:
                public_upload = upload_file_to_public_url(out_video, args, asset_type="generated_video")
                dump_json(job_dir / "public_upload.json", public_upload)
                return {
                    "plan": plan_item,
                    "ok": True,
                    "task_id": task_id,
                    "api_key": redact_key(api_key),
                    "ark_model": model_name,
                    "output_dir": str(job_dir),
                    "video_path": str(out_video),
                    "video_url": source_url,
                    "public_upload": public_upload,
                    "public_video_url": public_upload.get("public_url", "") if public_upload.get("ok") else "",
                    "attempt_count": attempt,
                    "disabled_api_keys": [redact_key(k) for k in disabled_keys],
                    "caption_generator_status": caption_result.get("status"),
                }
            raise RuntimeError("No video URL found in final Ark response")
        except Exception as exc:  # noqa: BLE001 - stored for review.
            last_error = f"{type(exc).__name__}: {exc}"
            if is_ark_input_sensitive_error(exc):
                safer_urls = ark_reference_urls_for_attempt(plan_item, use_safe_refs=True)
                if safer_urls and safer_urls != reference_urls:
                    reference_urls = safer_urls
                    force_safe_refs = True
                    attempts.append({
                        "attempt": attempt,
                        "api_key": redact_key(api_key),
                        "ark_model": model_name,
                        "retry_same_key": True,
                        "recovered_by": "switch_to_ark_safe_reference_images",
                        "reference_image_count": len(reference_urls),
                        "error": last_error[:1000],
                    })
                    dump_json(job_dir / "attempts.json", attempts)
                    continue
                # Every reference image is a real person and there is no person-free
                # alternative. Re-render the product on a white background (no human)
                # via IMAGE_GEN, then retry Ark with that converted reference.
                if getattr(args, "white_bg_fallback", True) and not white_bg_attempted:
                    white_bg_attempted = True
                    try:
                        white_bg_urls = convert_video_refs_to_white_bg(plan_item, args, job_dir)
                    except Exception as conv_exc:  # noqa: BLE001 - persisted for review.
                        white_bg_urls = []
                        print(f"[white-bg] conversion error for video_{plan_item['index']:02d}: {conv_exc}", flush=True)
                    if white_bg_urls and white_bg_urls != reference_urls:
                        reference_urls = white_bg_urls
                        force_safe_refs = True
                        attempts.append({
                            "attempt": attempt,
                            "api_key": redact_key(api_key),
                            "ark_model": model_name,
                            "retry_same_key": True,
                            "recovered_by": "white_bg_product_conversion",
                            "reference_image_count": len(reference_urls),
                            "error": last_error[:1000],
                        })
                        dump_json(job_dir / "attempts.json", attempts)
                        continue
            retry_same_key = not is_non_retryable_ark_create_error(exc)
            if not retry_same_key:
                disabled_keys.add(api_key)
            attempts.append({"attempt": attempt, "api_key": redact_key(api_key), "ark_model": model_name, "retry_same_key": retry_same_key, "reference_image_count": len(reference_urls), "using_safe_refs": force_safe_refs, "error": last_error[:1000]})
            dump_json(job_dir / "attempts.json", attempts)
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            if not retry_same_key and any(target["api_key"] not in disabled_keys for target in target_order):
                continue
            time.sleep(min(args.generation_retry_sleep_seconds, remaining))
    return {"plan": plan_item, "ok": False, "ark_targets": redacted_targets, "disabled_api_keys": [redact_key(k) for k in disabled_keys], "output_dir": str(job_dir), "error": last_error or "deadline exceeded", "attempt_count": attempt, "caption_generator_status": caption_result.get("status")}


def poll_ark_until_deadline(task_id: str, endpoint: str, api_key: str, output_dir: Path, prefix: str, deadline: float, sleep_seconds: int, timeout: int) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    poll_url = f"{endpoint.rstrip('/')}/{task_id}"
    poll_idx = 0
    final: Optional[Dict[str, Any]] = None
    while time.perf_counter() < deadline:
        poll_idx += 1
        time.sleep(min(sleep_seconds, max(0, deadline - time.perf_counter())))
        response = requests.get(poll_url, headers=headers, timeout=min(timeout, max(1, int(deadline - time.perf_counter()))))
        try:
            data = response.json()
        except Exception:
            data = {"raw_text": response.text}
        data["_http_status"] = response.status_code
        dump_json(output_dir / f"{prefix}_poll_{poll_idx:02d}.json", data)
        if response.status_code >= 400:
            raise RuntimeError(f"Ark poll failed: HTTP {response.status_code}: {response.text[:1000]}")
        status = ark_client.task_status(data)
        urls = ark_client.find_video_urls(data)
        print(f"[ark-video {prefix}] poll {poll_idx}: status={status or 'unknown'} video_urls={len(urls)}", flush=True)
        if urls or status in ark_client.TERMINAL_STATUSES:
            final = data
            break
    if final is None:
        raise TimeoutError(f"Ark task deadline exceeded: {task_id}")
    return final


def run_generation_jobs_concurrent(args: argparse.Namespace, run_dir: Path, case_id: str, brief: Dict[str, Any], final_caption: str, image_urls: List[str], image_downloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    reference_files = select_reference_files(image_downloads)
    if args.generate_image_assets and not reference_files:
        raise RuntimeError("Image asset generation requires at least one downloaded product reference image. Keep --download-selected-images enabled or fix image download failures.")
    if (args.submit_ark or args.video_count > 0) and not reference_files:
        print("[generation-plan] no downloaded reference image files; video planning can continue from URL references, but image asset generation is disabled", flush=True)
    plan = build_generation_plan(args, run_dir, case_id, brief, image_urls, reference_files)
    image_plan = plan.get("image_assets", [])
    video_plan = plan.get("videos", [])
    dump_json(run_dir / f"{case_id}_generation_plan.json", plan)
    print(f"generation_plan planner={plan.get('planner')} status={plan.get('planner_status')} images={len(image_plan)} videos={len(video_plan)}", flush=True)

    ark_targets = resolve_ark_key_model_pairs(args)
    if args.submit_ark and not ark_targets:
        raise RuntimeError("ARK_API_KEY or --ark-api-keys is required when submitting Ark tasks")
    if args.generate_image_assets and not args.image_gen_ak and "ak=" not in args.image_gen_endpoint:
        raise RuntimeError("IMAGE_GEN_AK or --image-gen-ak is required when generating image assets")

    results: Dict[str, Any] = {"image_assets": [], "videos": []}
    futures: Dict[Any, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(image_plan) + len(video_plan))) as executor:
        for item in image_plan:
            futures[executor.submit(run_image_asset_job, item, args, run_dir / "generated_image_assets")] = "image"
        for item in video_plan:
            futures[executor.submit(run_ark_video_job, item, ark_targets, final_caption, brief, args, run_dir / "generated_videos", case_id)] = "video"
        for future in as_completed(futures):
            typ = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            results["image_assets" if typ == "image" else "videos"].append(result)
            print(f"[{typ}:done] ok={result.get('ok')} output={result.get('output_dir', '')}", flush=True)
    public_assets = collect_public_asset_urls(results)
    results["public_assets"] = public_assets
    dump_json(run_dir / f"{case_id}_generation_results.json", results)
    dump_json(run_dir / f"{case_id}_public_asset_urls.json", public_assets)
    return results


def _copy_deliverable(src: Path, dst_dir: Path, prefix: str, index: int) -> Optional[Dict[str, Any]]:
    if not src.exists() or not src.is_file():
        return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{prefix}_{index:02d}{src.suffix or '.bin'}"
    dst = dst_dir / safe_name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return {"source_path": str(src), "deliverable_path": str(dst)}


def organize_media_deliverables(
    run_dir: Path,
    case_id: str,
    image_downloads: List[Dict[str, Any]],
    selected_original_9_16_assets: List[Dict[str, Any]],
    landing_video_downloads: List[Dict[str, Any]],
    generation_results: Dict[str, Any],
    args: Optional[argparse.Namespace] = None,
) -> Dict[str, Any]:
    """Copy user-facing media into one clean directory separate from prompts/plans/debug files."""
    deliverables_dir = run_dir / "media_deliverables"
    manifest: Dict[str, Any] = {
        "case_id": case_id,
        "deliverables_dir": str(deliverables_dir),
        "source_selected_images": [],
        "source_selected_images_9x16": [],
        "source_selected_videos_9x16": [],
        "generated_images": [],
        "generated_videos": [],
        "public_assets": generation_results.get("public_assets", {}),
    }
    for idx, item in enumerate([x for x in image_downloads if x.get("ok") and x.get("path")], 1):
        copied = _copy_deliverable(Path(item["path"]), deliverables_dir / "source_selected_images", "source_image", idx)
        if copied:
            manifest["source_selected_images"].append({**item, **copied})
    for idx, item in enumerate([x for x in selected_original_9_16_assets if x.get("path")], 1):
        copied = _copy_deliverable(Path(item["path"]), deliverables_dir / "source_selected_images_9x16", "source_image_9x16", idx)
        if copied:
            manifest_item = {**item, **copied}
            if args is not None and getattr(args, "upload_source_assets", True):
                upload = upload_file_to_public_url(Path(copied["deliverable_path"]), args, asset_type="source_selected_image_9x16")
                manifest_item["public_upload"] = upload
                if upload.get("ok") and upload.get("public_url"):
                    manifest_item["public_url"] = upload.get("public_url")
            manifest["source_selected_images_9x16"].append(manifest_item)
    for idx, item in enumerate([x for x in landing_video_downloads if x.get("ok") and x.get("path")], 1):
        copied = _copy_deliverable(Path(item["path"]), deliverables_dir / "source_selected_videos_9x16", "source_video_9x16", idx)
        if copied:
            manifest_item = {**item, **copied}
            if args is not None and getattr(args, "upload_source_assets", True):
                upload = upload_file_to_public_url(Path(copied["deliverable_path"]), args, asset_type="source_selected_video_9x16")
                manifest_item["public_upload"] = upload
                if upload.get("ok") and upload.get("public_url"):
                    manifest_item["public_url"] = upload.get("public_url")
            manifest["source_selected_videos_9x16"].append(manifest_item)
    gen_image_idx = 0
    for item in generation_results.get("image_assets", []) or []:
        for saved_path in item.get("saved_paths", []) or []:
            gen_image_idx += 1
            copied = _copy_deliverable(Path(saved_path), deliverables_dir / "generated_images", "generated_image", gen_image_idx)
            if copied:
                manifest["generated_images"].append({"plan": item.get("plan"), "public_urls": item.get("public_urls", []), **copied})
    for idx, item in enumerate([x for x in generation_results.get("videos", []) or [] if x.get("ok") and x.get("video_path")], 1):
        copied = _copy_deliverable(Path(item["video_path"]), deliverables_dir / "generated_videos", "generated_video", idx)
        if copied:
            manifest["generated_videos"].append({"plan": item.get("plan"), "video_url": item.get("video_url"), "public_video_url": item.get("public_video_url", ""), **copied})
    dump_json(deliverables_dir / "manifest.json", manifest)
    dump_json(run_dir / f"{case_id}_media_deliverables_manifest.json", manifest)
    return manifest


# Objective-driven generation presets. These set creative shape (aspect ratio,
# duration, and how many image/video variants to produce) from the campaign goal
# instead of the one-size-fits-all 9:16 / 15s / 3 videos / 6 images defaults.
# Presets only fill parameters the user did NOT pass explicitly on the command
# line, so any flag the user sets always wins.
OBJECTIVE_PRESETS: Dict[str, Dict[str, Any]] = {
    "conversion":  {"ratio": "9:16", "duration": 15, "video_count": 3, "image_asset_count": 6, "max_images": 6},
    "traffic":     {"ratio": "9:16", "duration": 15, "video_count": 3, "image_asset_count": 6, "max_images": 6},
    "app_install": {"ratio": "9:16", "duration": 15, "video_count": 3, "image_asset_count": 6, "max_images": 6},
    # Awareness/engagement favor shorter, hook-forward creative and more variants
    # to test hooks, so use shorter duration and a higher variant count.
    "awareness":   {"ratio": "9:16", "duration": 9,  "video_count": 4, "image_asset_count": 8, "max_images": 8},
    "engagement":  {"ratio": "9:16", "duration": 9,  "video_count": 4, "image_asset_count": 8, "max_images": 8},
}

_OBJECTIVE_PRESET_FLAGS = {
    "ratio": "--ratio",
    "duration": "--duration",
    "video_count": "--video-count",
    "image_asset_count": "--image-asset-count",
    "max_images": "--max-images",
}


def apply_objective_presets(args: argparse.Namespace, argv: Optional[List[str]] = None) -> List[str]:
    """Fill generation params from --objective, never overriding explicit flags.

    Returns a human-readable list of the parameters that were set by the preset.
    """
    objective = str(getattr(args, "objective", "") or "none").strip().lower()
    preset = OBJECTIVE_PRESETS.get(objective)
    if not preset:
        return []
    tokens = list(sys.argv[1:] if argv is None else argv)

    def passed_explicitly(flag: str) -> bool:
        return any(tok == flag or tok.startswith(flag + "=") for tok in tokens)

    applied: List[str] = []
    for attr, value in preset.items():
        flag = _OBJECTIVE_PRESET_FLAGS[attr]
        if passed_explicitly(flag):
            continue
        setattr(args, attr, value)
        applied.append(f"{flag}={value}")
    return applied


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Creative URL/CSV to Ark 9:16 video pipeline")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--structured-input", default="", help="Existing structured crawler CSV. If omitted, defaults to latest full CSV unless --raw-input/--url is provided.")
    source.add_argument("--raw-input", default="", help="Raw CSV with raw_url column; crawler will run first.")
    source.add_argument("--url", default="", help="Single URL; crawler will run first using a temporary one-row CSV.")
    parser.add_argument("--case-id", default="", help="case_id to use from structured CSV or assign to --url. Defaults to single_url for --url, 525055 for existing CSV.")
    parser.add_argument("--row-index", type=int, default=0, help="Row index to use when --case-id is empty")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Base output directory")
    parser.add_argument("--run-name", default="", help="Optional deterministic run folder name")
    parser.add_argument("--max-images", type=int, default=6, help="Reference images selected from the crawler for planning; each video variant uses a rotated subset")

    parser.add_argument("--crawl-output", default="", help="Optional crawler output CSV path")
    parser.add_argument("--limit", type=int, default=0, help="Crawler row limit when crawling raw input")
    parser.add_argument("--image-mode", choices=["strict", "balanced", "recall"], default="strict")
    parser.add_argument("--visual-candidate-limit", type=int, default=10, help="Base number of crawler-ranked image candidates before recall expansion")
    parser.add_argument("--max-creative-images", type=int, default=12)
    parser.add_argument("--text-heavy-policy", choices=["separate", "exclude", "include"], default="separate")
    parser.add_argument("--structured-review", choices=["modelhub"], default="modelhub", help="Clean URL structured fields with ModelHub LLM only")
    parser.add_argument("--structured-review-endpoint", default=os.environ.get("STRUCTURED_REVIEW_ENDPOINT", default_modelhub_endpoint_from_env()))
    parser.add_argument("--structured-review-model", default=os.environ.get("STRUCTURED_REVIEW_MODEL", os.environ.get("GENERATION_PLANNER_MODEL", "gemini-3.5-flash")))
    parser.add_argument("--structured-review-logid", default="")
    parser.add_argument("--structured-review-timeout", type=int, default=120)
    parser.add_argument("--structured-review-max-tokens", type=int, default=63000)
    parser.add_argument("--write-image-debug-files", action="store_true")
    parser.add_argument("--download-visual-candidates", action="store_true")
    parser.add_argument("--visual-review", choices=["none", "heuristic", "openai", "modelhub", "manual"], default="none")
    parser.add_argument("--visual-model", default="gemini-3.5-flash")
    parser.add_argument("--visual-review-batch-size", type=int, default=10, help="Images per visual-model request; capped at 10")
    parser.add_argument("--opaque-shopify-recall-limit", type=int, default=20, help="Extra high-res opaque Shopify images sent to visual review beyond base limit")
    parser.add_argument("--enable-web-search", action="store_true", dest="enable_web_search", default=True, help="Enable Coze/DuckDuckGo supplemental same-brand same-product web/social search for URL generation (default: on)")
    parser.add_argument("--no-enable-web-search", "--no-web-search", action="store_false", dest="enable_web_search", help="Disable supplemental web/social search for URL generation")
    parser.add_argument("--web-search-provider", choices=["coze", "duckduckgo"], default="coze")
    parser.add_argument("--web-fetch-mode", choices=["static", "rendered", "auto"], default="auto")
    parser.add_argument("--web-search-limit", type=int, default=6)
    parser.add_argument("--web-image-limit", type=int, default=20)
    parser.add_argument("--coze-token", default=os.environ.get("COZE_API_TOKEN"))

    parser.add_argument("--download-selected-images", action="store_true", default=True, help="Download selected Ark reference images for local review")
    parser.add_argument("--no-download-selected-images", action="store_false", dest="download_selected_images")
    parser.add_argument("--pad-selected-original-images", action="store_true", default=True, help="For downloaded selected original URL images, output separate 9:16 padded assets when source ratio is not 9:16")
    parser.add_argument("--no-pad-selected-original-images", action="store_false", dest="pad_selected_original_images")
    parser.add_argument("--selected-original-padding-fill", choices=["blur", "solid"], default="blur")
    parser.add_argument("--image-download-workers", type=int, default=6)
    parser.add_argument("--image-download-retries", type=int, default=2)
    parser.add_argument("--image-download-timeout", type=int, default=20)

    parser.add_argument("--download-landing-videos", action="store_true", default=True, help="Download landing-page video URLs and keep only videos whose actual dimensions are 9:16; videos are not padded")
    parser.add_argument("--no-download-landing-videos", action="store_false", dest="download_landing_videos")
    parser.add_argument("--video-download-workers", type=int, default=4)
    parser.add_argument("--video-download-retries", type=int, default=2)
    parser.add_argument("--video-download-timeout", type=int, default=90)

    parser.add_argument("--submit-ark", action="store_true", help="Actually submit to Ark. Without this, writes payload only")
    parser.add_argument("--ark-endpoint", default=os.environ.get("ARK_ENDPOINT", ark_client.DEFAULT_ENDPOINT))
    parser.add_argument("--ark-api-keys", default=os.environ.get("ARK_API_KEYS", ""), help="Comma/newline separated Ark API keys. Video tasks are round-robin assigned across keys")
    parser.add_argument("--ark-model", default=ark_client.DEFAULT_MODEL)
    parser.add_argument("--ark-model-names", default=os.environ.get("ARK_MODEL_NAME", os.environ.get("ARK_MODEL_NAMES", "")), help="Comma/newline separated Ark model names paired by index with --ark-api-keys / ARK_API_KEYS. Uses --ark-model only when a paired model entry is omitted")
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--no-generate-audio", action="store_true")
    parser.add_argument("--max-polls", type=int, default=45)
    parser.add_argument("--ark-timeout", type=int, default=120)
    parser.add_argument("--ark-poll-interval-seconds", type=int, default=10)

    parser.add_argument("--generate-image-assets", action="store_true", help="Generate TikTok image creatives in parallel with video tasks")
    parser.add_argument("--image-asset-count", type=int, default=6)
    parser.add_argument("--image-gen-endpoint", default=os.environ.get("IMAGE_GEN_ENDPOINT", image_asset_generator.DEFAULT_EDIT_ENDPOINT))
    parser.add_argument("--image-gen-base-url", default=os.environ.get("IMAGE_GEN_BASE_URL", ""), help="OpenAI-compatible base URL for SDK image edit mode. Defaults to deriving from --image-gen-endpoint")
    parser.add_argument("--image-gen-reference-mode", choices=["sdk_edit", "edit", "generation_image_field"], default=os.environ.get("IMAGE_GEN_REFERENCE_MODE", "sdk_edit"), help="How to send reference images. Default sdk_edit uses OpenAI SDK client.images.edit so image tokens are consumed; generation_image_field keeps old debug behavior")
    parser.add_argument("--image-gen-ak", default=os.environ.get("IMAGE_GEN_AK", ""))
    parser.add_argument("--image-gen-model", default=image_asset_generator.DEFAULT_MODEL)
    parser.add_argument("--image-gen-size", default="1152x2048")
    parser.add_argument("--image-gen-quality", default="high")
    parser.add_argument("--image-gen-timeout", type=int, default=120)
    parser.add_argument("--video-count", type=int, default=3)
    parser.add_argument("--white-bg-fallback", action="store_true", default=True, help="When every Ark video reference image is a real person and Ark rejects it as sensitive/privacy content, re-render the product on a white background (no human) via IMAGE_GEN and retry Ark with that converted reference. Default on.")
    parser.add_argument("--no-white-bg-fallback", action="store_false", dest="white_bg_fallback")
    parser.add_argument("--white-bg-max-refs", type=int, default=1, help="How many person reference images to convert into white-background product packshots per video when the white-bg fallback fires")
    parser.add_argument(
        "--objective",
        choices=["none", "conversion", "traffic", "app_install", "awareness", "engagement"],
        default=os.environ.get("CREATIVE_OBJECTIVE", "none"),
        help="Campaign objective preset that sets ratio/duration/video-count/image-count when those flags are not passed explicitly. 'none' keeps the bare defaults. conversion/traffic/app_install=15s x3; awareness/engagement=9s x4 with more variants.",
    )
    parser.add_argument("--caption-brief-generator", choices=["modelhub"], default="modelhub", help="Generate caption brief with ModelHub LLM only")
    parser.add_argument("--caption-brief-endpoint", default=os.environ.get("CAPTION_BRIEF_ENDPOINT", os.environ.get("CAPTION_GENERATOR_ENDPOINT", default_modelhub_endpoint_from_env())))
    parser.add_argument("--caption-brief-model", default=os.environ.get("CAPTION_BRIEF_MODEL", os.environ.get("CAPTION_GENERATOR_MODEL", os.environ.get("GENERATION_PLANNER_MODEL", "gemini-3.5-flash"))))
    parser.add_argument("--caption-brief-logid", default="")
    parser.add_argument("--caption-brief-timeout", type=int, default=120)
    parser.add_argument("--caption-brief-max-tokens", type=int, default=63000)
    parser.add_argument("--caption-generator", choices=["modelhub"], default="modelhub", help="Generate final Ark caption with ModelHub LLM only")
    parser.add_argument("--video-caption-generator", choices=["modelhub"], default=os.environ.get("VIDEO_CAPTION_GENERATOR", "modelhub"), help="Generate each submitted video variant's final Ark caption with ModelHub LLM only")
    parser.add_argument("--caption-generator-endpoint", default=os.environ.get("CAPTION_GENERATOR_ENDPOINT", default_modelhub_endpoint_from_env()))
    parser.add_argument("--caption-generator-model", default=os.environ.get("CAPTION_GENERATOR_MODEL", os.environ.get("GENERATION_PLANNER_MODEL", "gemini-3.5-flash")))
    parser.add_argument("--caption-generator-logid", default="")
    parser.add_argument("--caption-generator-timeout", type=int, default=120)
    parser.add_argument("--caption-generator-max-tokens", type=int, default=63000)
    parser.add_argument("--generation-planner", choices=["modelhub"], default="modelhub", help="Plan image/video variants with ModelHub LLM only")
    parser.add_argument("--generation-planner-endpoint", default=default_modelhub_endpoint_from_env())
    parser.add_argument("--generation-planner-model", default=os.environ.get("GENERATION_PLANNER_MODEL", "gemini-3.5-flash"))
    parser.add_argument("--generation-planner-logid", default="")
    parser.add_argument("--generation-planner-timeout", type=int, default=120)
    parser.add_argument("--generation-planner-max-tokens", type=int, default=63000)
    parser.add_argument("--benchmark-output-dir", default="", help="Optional ad-creative-benchmark output directory containing video_analysis.json. When supplied, video plans may selectively adapt matching high-quality reference-video shooting patterns.")
    parser.add_argument("--benchmark-video-analysis", default="", help="Optional explicit path to video_analysis.json from the benchmark reference-video analysis workflow")
    parser.add_argument("--reference-pattern-policy", choices=["auto", "off", "force"], default="auto", help="How generation planning uses benchmark reference patterns: auto uses only relevant patterns, off ignores them, force requires the file and asks the planner to consider them")
    parser.add_argument("--max-reference-patterns", type=int, default=0, help="Maximum benchmark reference patterns passed to the planner. 0 means use --video-count")
    parser.add_argument("--reference-pattern-min-confidence", type=float, default=0.6, help="Minimum MLLM confidence for benchmark patterns in auto mode")
    parser.add_argument("--parallel-benchmark-command", default="", help="Optional shell command to start the URL/adv benchmark + reference-pattern workflow in parallel while this script crawls and structures URL information. It should eventually write video_analysis.json under --benchmark-output-dir or --benchmark-video-analysis.")
    parser.add_argument("--parallel-benchmark-cwd", default="", help="Working directory for --parallel-benchmark-command. Defaults to the caller's current directory.")
    parser.add_argument("--benchmark-wait-seconds", type=int, default=0, help="Seconds to wait for benchmark video_analysis.json before generation planning. 0 means do not wait; use patterns only if already ready.")
    parser.add_argument("--parallel-benchmark-fail-policy", choices=["warn", "fail"], default="warn", help="If the parallel benchmark command fails or times out before patterns are ready, warn and continue by default, or fail the generation run.")
    parser.add_argument("--llm-retry-attempts", type=int, default=DEFAULT_LLM_RETRY_ATTEMPTS, help="Sleep/retry attempts for every ModelHub/OpenAI-compatible LLM call. Default from LLM_RETRY_ATTEMPTS or 20")
    parser.add_argument("--llm-retry-sleep-seconds", type=float, default=DEFAULT_LLM_RETRY_SLEEP_SECONDS, help="Sleep seconds between LLM retry attempts. Default from LLM_RETRY_SLEEP_SECONDS or 10")
    parser.add_argument("--generation-max-wait-seconds", type=int, default=1800, help="Overall deadline for each image/video job; retry until success or deadline. Default 1800s = 30 minutes")
    parser.add_argument("--generation-retry-sleep-seconds", type=int, default=10)
    parser.add_argument("--upload-generated-assets", action="store_true", default=True, help="Upload generated image/video files to public URLs after local files are produced")
    parser.add_argument("--no-upload-generated-assets", action="store_false", dest="upload_generated_assets")
    parser.add_argument("--upload-source-assets", action="store_true", default=True, help="Upload campaign-ready selected source assets (selected_original_9x16_assets and landing_page_videos_9x16) to public URLs when organizing deliverables")
    parser.add_argument("--no-upload-source-assets", action="store_false", dest="upload_source_assets")
    parser.add_argument("--public-upload-provider", choices=["humanaigc", "sparrow"], default=os.environ.get("PUBLIC_UPLOAD_PROVIDER", "humanaigc"), help="Public asset uploader. humanaigc is the default CDN-backed TOS uploader; sparrow is legacy fallback")
    parser.add_argument("--public-upload-python-path", default=os.environ.get("PUBLIC_UPLOAD_PYTHON_PATH", "/Users/bytedance/Desktop/agentic_ad_creation/sparrow/python"), help="Legacy sparrow provider path containing logic.e2e_video_gen.utils.upload_tos")
    parser.add_argument("--public-upload-site-packages", default=os.environ.get("PUBLIC_UPLOAD_SITE_PACKAGES", DEFAULT_PUBLIC_UPLOAD_SITE_PACKAGES), help="Optional site-packages path containing bytedtos")
    parser.add_argument("--internal-pypi-index", default=DEFAULT_INTERNAL_PYPI_INDEX, help="Internal PyPI index used to auto-install bytedtos for public CDN upload. Default https://bytedpypi.byted.org/simple/ (override with INTERNAL_PYPI_INDEX)")
    parser.add_argument("--auto-install-deps", action="store_true", default=True, help="Auto-install bytedtos from the internal PyPI index when public upload needs it. Default on.")
    parser.add_argument("--no-auto-install-deps", action="store_false", dest="auto_install_deps", help="Disable bytedtos auto-install; fail with manual install instructions instead.")
    parser.add_argument("--public-upload-key-prefix", default=os.environ.get("PUBLIC_UPLOAD_KEY_PREFIX", "creative_url_video_pipeline/generated"), help="TOS key prefix for uploaded generated assets")
    parser.add_argument("--public-upload-retries", type=int, default=int(os.environ.get("PUBLIC_UPLOAD_RETRIES", "3")))
    parser.add_argument("--public-upload-retry-sleep-seconds", type=float, default=float(os.environ.get("PUBLIC_UPLOAD_RETRY_SLEEP_SECONDS", "3")))
    parser.add_argument("--public-tos-bucket", default=os.environ.get("PUBLIC_TOS_BUCKET", DEFAULT_PUBLIC_TOS_BUCKET))
    parser.add_argument("--public-tos-ak", default=os.environ.get("PUBLIC_TOS_AK", ""))
    parser.add_argument("--public-tos-sk", default=os.environ.get("PUBLIC_TOS_SK", ""))
    parser.add_argument("--public-tos-endpoint", default=os.environ.get("PUBLIC_TOS_ENDPOINT", DEFAULT_PUBLIC_TOS_ENDPOINT))
    parser.add_argument("--public-tos-service", default=os.environ.get("PUBLIC_TOS_SERVICE", DEFAULT_PUBLIC_TOS_SERVICE))
    parser.add_argument("--public-tos-cluster", default=os.environ.get("PUBLIC_TOS_CLUSTER", DEFAULT_PUBLIC_TOS_CLUSTER))
    parser.add_argument("--public-tos-idc", default=os.environ.get("PUBLIC_TOS_IDC", DEFAULT_PUBLIC_TOS_IDC))
    parser.add_argument("--public-tos-cdn-prefix", default=os.environ.get("PUBLIC_TOS_CDN_PREFIX", DEFAULT_PUBLIC_TOS_CDN_PREFIX))
    parser.add_argument("--public-tos-timeout", type=int, default=int(os.environ.get("PUBLIC_TOS_TIMEOUT", "120")))
    parser.add_argument("--public-tos-connect-timeout", type=int, default=int(os.environ.get("PUBLIC_TOS_CONNECT_TIMEOUT", "60")))
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    applied_presets = apply_objective_presets(args)
    if applied_presets:
        print(f"[objective] preset '{args.objective}' set {', '.join(applied_presets)} (explicit flags untouched)", flush=True)
    args.effective_case_id = args.case_id or ("single_url" if args.url else "525055")
    if args.enable_web_search and args.web_search_provider == "coze" and not (args.coze_token or "").strip():
        raise RuntimeError(
            "URL generation enables Coze web search by default. Export COZE_API_TOKEN, "
            "or pass --no-web-search / --web-search-provider duckduckgo to disable or change the search backend."
        )
    timer = StageTimer()

    run_name = args.run_name or f"{args.effective_case_id or 'row'}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    run_dir = Path(args.out_dir) / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    args._parallel_benchmark_proc = start_parallel_benchmark_process(args, run_dir)

    with timer.stage("prepare_input"):
        if args.raw_input or args.url:
            structured_input: Path
            with timer.stage("crawl_url_or_csv"):
                structured_input = run_crawler(args, run_dir)
        else:
            structured_input = Path(args.structured_input) if args.structured_input else DEFAULT_STRUCTURED_INPUT
        if not structured_input.exists():
            raise FileNotFoundError(f"structured input not found: {structured_input}")
        print(f"structured_input={structured_input}", flush=True)

    with timer.stage("build_caption_brief_and_ark_caption"):
        row = caption_builder.load_row(structured_input, args.effective_case_id or None, args.row_index)
        brief_input = caption_builder.build_brief_input(row, args.max_images)
        case_id = brief_input.get("case_id") or args.effective_case_id or "row"
        if args.caption_brief_generator != "modelhub":
            raise RuntimeError("caption_brief_generator must be modelhub; local brief generation has been removed")
        brief_prompt = build_caption_brief_prompt(brief_input)
        (run_dir / f"{case_id}_caption_brief_prompt.txt").write_text(brief_prompt, encoding="utf-8")
        dump_json(run_dir / f"{case_id}_caption_brief_input.json", brief_input)
        brief = call_modelhub_caption_brief(brief_prompt, args, run_dir, case_id, brief_input)
        generation_prompt = caption_builder.build_generation_prompt(row, brief)
        brief_caption = caption_builder.build_final_ark_caption(brief)
        if args.caption_generator != "modelhub":
            raise RuntimeError("caption_generator must be modelhub; local caption generation has been removed")
        caption_generator_result = call_modelhub_caption_generator(generation_prompt, brief_caption, args, run_dir, case_id)
        final_caption = norm_text(caption_generator_result.get("final_ark_caption"))
        if not final_caption:
            raise RuntimeError("caption generator returned empty final_ark_caption")
        caption_generator_result["generator"] = "modelhub"
        caption_generator_result["status"] = "modelhub_completed"
        dump_json(run_dir / f"{case_id}_caption_generator_result.json", caption_generator_result)
        caption_paths = write_caption_outputs(run_dir, case_id, brief, generation_prompt, final_caption)
        image_urls = [asset["url"] for asset in brief.get("selected_images", []) if asset.get("url")]
        print(f"selected_images={len(image_urls)}", flush=True)
        for i, url in enumerate(image_urls, 1):
            print(f"  Image {i}: {url}", flush=True)

    image_downloads: List[Dict[str, Any]] = []
    selected_original_9_16_assets: List[Dict[str, Any]] = []
    landing_video_downloads: List[Dict[str, Any]] = []
    generation_results: Dict[str, Any] = {"image_assets": [], "videos": [], "public_assets": {}}
    if args.download_selected_images:
        with timer.stage("download_selected_images_concurrent"):
            image_downloads = download_selected_images(
                image_urls,
                run_dir / "selected_images",
                workers=args.image_download_workers,
                timeout=args.image_download_timeout,
                retries=args.image_download_retries,
            )
            dump_json(run_dir / f"{case_id}_selected_image_downloads.json", image_downloads)
            ok_count = sum(1 for item in image_downloads if item.get("ok"))
            print(f"downloaded_selected_images={ok_count}/{len(image_downloads)}", flush=True)
        if args.pad_selected_original_images:
            with timer.stage("pad_selected_original_images_to_9_16"):
                selected_original_9_16_assets = make_selected_original_9_16_assets(
                    image_downloads,
                    run_dir / "selected_original_9x16_assets",
                    fill=args.selected_original_padding_fill,
                )
                dump_json(run_dir / f"{case_id}_selected_original_9x16_assets.json", selected_original_9_16_assets)
                padded_count = sum(1 for item in selected_original_9_16_assets if item.get("padded"))
                already_count = sum(1 for item in selected_original_9_16_assets if item.get("source_is_9_16"))
                print(f"selected_original_9x16_assets={len(selected_original_9_16_assets)} padded={padded_count} already_9x16={already_count}", flush=True)

    if args.download_landing_videos:
        landing_video_urls = landing_page_video_urls_from_row(row)
        with timer.stage("download_landing_videos_filter_9_16"):
            landing_video_downloads = download_landing_page_videos_9_16(
                landing_video_urls,
                run_dir / "landing_page_videos_9x16",
                workers=args.video_download_workers,
                timeout=args.video_download_timeout,
                retries=args.video_download_retries,
            )
            dump_json(run_dir / f"{case_id}_landing_video_downloads_9x16.json", landing_video_downloads)
            ok_count = sum(1 for item in landing_video_downloads if item.get("ok"))
            print(f"landing_page_videos_9x16={ok_count}/{len(landing_video_downloads)}", flush=True)

    with timer.stage("build_ark_payload"):
        preview_models = parse_csv_list(args.ark_model_names) or [args.ark_model]
        preview_model = preview_models[0] if preview_models else args.ark_model
        payload = ark_client.build_payload(
            final_caption,
            image_urls,
            model=preview_model,
            duration=args.duration,
            ratio=args.ratio,
            generate_audio=not args.no_generate_audio,
        )
        preview_targets = resolve_ark_key_model_pairs(args)
        dump_json(run_dir / f"{case_id}_ark_request_redacted.json", {
            "endpoint": args.ark_endpoint,
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer [REDACTED]"},
            "ark_targets": [{"api_key": redact_key(t["api_key"]), "model": t["model"]} for t in preview_targets],
            "payload": payload,
        })

    if args.submit_ark or args.generate_image_assets:
        with timer.stage("parallel_generate_images_and_videos"):
            generation_results = run_generation_jobs_concurrent(args, run_dir, case_id, brief, final_caption, image_urls, image_downloads)
            image_ok = sum(1 for item in generation_results.get("image_assets", []) if item.get("ok"))
            video_ok = sum(1 for item in generation_results.get("videos", []) if item.get("ok"))
            print(f"generated_image_assets={image_ok}/{len(generation_results.get('image_assets', []))}", flush=True)
            print(f"generated_videos={video_ok}/{len(generation_results.get('videos', []))}", flush=True)
    else:
        print("\nDry run only. Add --submit-ark and/or --generate-image-assets to generate assets.", flush=True)

    with timer.stage("organize_media_deliverables"):
        deliverables_manifest = organize_media_deliverables(
            run_dir,
            case_id,
            image_downloads,
            selected_original_9_16_assets,
            landing_video_downloads,
            generation_results,
            args,
        )
        print(f"media_deliverables={deliverables_manifest['deliverables_dir']}", flush=True)

    total = sum(timer.metrics.values())
    metrics = {"stages_seconds": timer.metrics, "total_recorded_stage_seconds": total, "run_dir": str(run_dir)}
    dump_json(run_dir / "timing_metrics.json", metrics)
    print(f"\nrun_dir={run_dir}", flush=True)
    print(f"timing_metrics={run_dir / 'timing_metrics.json'}", flush=True)
    print(f"caption={caption_paths['caption']}", flush=True)
    print(f"payload={run_dir / f'{case_id}_ark_request_redacted.json'}", flush=True)
    print(f"media_deliverables={run_dir / 'media_deliverables'}", flush=True)
    close_parallel_benchmark_files(getattr(args, "_parallel_benchmark_proc", None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
