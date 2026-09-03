#!/usr/bin/env python3
"""Generate TikTok-ready image creatives with reference images and retry.

This is intentionally small and standalone so it can be called after the URL crawler
has selected product/reference images. It uses the internal ModelHub OpenAI-compatible
image generation endpoint.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    from PIL import Image, ImageFilter
except Exception:  # pragma: no cover - optional postprocess dependency.
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - handled at runtime with clear error.
    OpenAI = None  # type: ignore

DEFAULT_BASE_URL = "https://aidp.bytedance.net/gpt/openapi/online/v2/crawl/openai"
DEFAULT_ENDPOINT = DEFAULT_BASE_URL + "/images/generations"
DEFAULT_EDIT_ENDPOINT = DEFAULT_BASE_URL + "/images/edits"
DEFAULT_MODEL = "gpt-image-2"


def read_image_as_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def read_image_as_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def infer_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")


def endpoint_for_reference_mode(endpoint: str, mode: str) -> str:
    """Return an endpoint that is expected to consume reference images.

    The `/images/generations` gateway accepts unknown JSON keys but, in observed
    responses, reports `usage.input_tokens_details.image_tokens = 0` even when an
    `image` field is present. For reference-guided generation, prefer the OpenAI
    image edit endpoint shape.
    """
    if mode == "edit" and "/images/generations" in endpoint:
        return endpoint.replace("/images/generations", "/images/edits")
    return endpoint


def base_url_from_endpoint(endpoint: str) -> str:
    cleaned = endpoint.split("?", 1)[0].rstrip("/")
    for suffix in ("/images/generations", "/images/edits"):
        if cleaned.endswith(suffix):
            return cleaned[: -len(suffix)]
    return cleaned


def build_reference_payload(
    model: str,
    prompt: str,
    image_paths: List[Path],
    size: str,
    quality: str,
    n: int,
    mode: str,
) -> Dict[str, Any]:
    if not image_paths:
        raise ValueError("At least one reference image is required; refusing text-only image generation")
    if mode == "generation_image_field":
        refs = [{"type": "image_url", "image_url": {"url": read_image_as_data_url(path)}} for path in image_paths]
        return {"model": model, "prompt": prompt, "n": n, "size": size, "quality": quality, "image": refs}
    # Default edit-compatible shape: images are direct data URLs in `image`.
    # This mirrors OpenAI image edit semantics more closely than chat-style
    # image_url objects under `/images/generations`.
    return {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
        "quality": quality,
        "image": [read_image_as_data_url(path) for path in image_paths],
    }


def redact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    redacted = json.loads(json.dumps(payload, ensure_ascii=False))
    refs = redacted.get("image") or redacted.get("images") or []
    if isinstance(refs, list):
        for idx, item in enumerate(refs):
            if isinstance(item, str) and item.startswith("data:"):
                refs[idx] = "data:image/[REDACTED_BASE64]"
                continue
            if isinstance(item, dict):
                try:
                    url = item.get("image_url", {}).get("url", "")
                    if isinstance(url, str) and url.startswith("data:"):
                        item["image_url"]["url"] = "data:image/[REDACTED_BASE64]"
                except Exception:
                    pass
    return redacted


def is_resource_insufficient_payload(data: Any) -> bool:
    """Detect transient provider capacity errors from a parsed response body."""
    try:
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    except Exception:
        text = str(data)
    lowered = text.lower()
    return "-4302" in text or "资源不足" in text or "resource insufficient" in lowered or "resource pool" in lowered


def is_resource_insufficient(response: requests.Response, data: Any = None) -> bool:
    """Detect provider capacity errors even when the gateway returns HTTP 200."""
    if data is not None and is_resource_insufficient_payload(data):
        return True
    if response.status_code == 429:
        try:
            return is_resource_insufficient_payload(response.json())
        except Exception:
            return True
    return False


def extract_generated_images(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return generated image refs from common OpenAI-compatible response shapes."""
    out: List[Dict[str, str]] = []
    for item in data.get("data") or []:
        if isinstance(item, dict):
            if item.get("url"):
                out.append({"type": "url", "value": item["url"]})
            if item.get("b64_json"):
                out.append({"type": "b64_json", "value": item["b64_json"]})
    # Some gateways wrap under choices/message/content.
    for choice in data.get("choices") or []:
        content = (((choice or {}).get("message") or {}).get("content"))
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                image_url = (part.get("image_url") or {}).get("url") if isinstance(part.get("image_url"), dict) else None
                if image_url:
                    if image_url.startswith("data:image"):
                        out.append({"type": "data_url", "value": image_url})
                    else:
                        out.append({"type": "url", "value": image_url})
    return out


def save_generated_image(ref: Dict[str, str], out_path: Path, timeout: int = 60) -> Optional[Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    typ = ref.get("type")
    val = ref.get("value") or ""
    if typ == "url":
        r = requests.get(val, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return out_path
    if typ == "b64_json":
        out_path.write_bytes(base64.b64decode(val))
        return out_path
    if typ == "data_url":
        m = re.match(r"data:[^;]+;base64,(.*)$", val, flags=re.S)
        if not m:
            return None
        out_path.write_bytes(base64.b64decode(m.group(1)))
        return out_path
    return None


def parse_size(size: str) -> Optional[tuple[int, int]]:
    m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", size or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def fit_image_to_canvas(path: Path, target_size: str) -> Optional[Dict[str, Any]]:
    """Pad/crop the generated image to the requested vertical canvas.

    Some internal gateways accept a portrait size such as 1024x1792 but return a
    supported native size such as 1024x1536. For TikTok assets we keep the model
    output intact as foreground and extend the canvas with a blurred background
    instead of cropping/recomposing the product.
    """
    target = parse_size(target_size)
    if not target or Image is None or ImageFilter is None:
        return None
    target_w, target_h = target
    with Image.open(path) as im0:
        im = im0.convert("RGB")
        src_w, src_h = im.size
        if (src_w, src_h) == (target_w, target_h):
            return {"path": str(path), "source_size": [src_w, src_h], "target_size": [target_w, target_h], "action": "none"}

        raw_path = path.with_name(path.stem + "_raw" + path.suffix)
        path.write_bytes(path.read_bytes())  # keep original output as the working input until final save
        if not raw_path.exists():
            raw_path.write_bytes(path.read_bytes())

        # Background: center-crop a cover-scaled copy and blur it. Foreground:
        # contain-fit the original so product appearance is not cropped.
        cover_scale = max(target_w / src_w, target_h / src_h)
        cover_size = (max(1, int(round(src_w * cover_scale))), max(1, int(round(src_h * cover_scale))))
        bg = im.resize(cover_size, Image.Resampling.LANCZOS)
        left = max(0, (bg.width - target_w) // 2)
        top = max(0, (bg.height - target_h) // 2)
        bg = bg.crop((left, top, left + target_w, top + target_h)).filter(ImageFilter.GaussianBlur(radius=24))

        contain_scale = min(target_w / src_w, target_h / src_h)
        fg_size = (max(1, int(round(src_w * contain_scale))), max(1, int(round(src_h * contain_scale))))
        fg = im.resize(fg_size, Image.Resampling.LANCZOS)
        x = (target_w - fg.width) // 2
        y = (target_h - fg.height) // 2
        bg.paste(fg, (x, y))
        bg.save(path)
        return {
            "path": str(path),
            "raw_path": str(raw_path),
            "source_size": [src_w, src_h],
            "target_size": [target_w, target_h],
            "action": "blur_pad_contain",
        }


def generate_with_retry(
    endpoint: str,
    ak: str,
    payload: Dict[str, Any],
    logid: str,
    max_attempts: int,
    sleep_seconds: int,
    timeout: int,
    output_dir: Path,
    max_seconds: int = 0,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    url = endpoint if "?" in endpoint else f"{endpoint}?ak={ak}"
    headers = {"Content-Type": "application/json", "X-TT-LOGID": logid}
    attempts: List[Dict[str, Any]] = []
    last_data: Any = None
    started_all = time.perf_counter()
    deadline = started_all + max_seconds if max_seconds and max_seconds > 0 else None
    attempt = 0
    while True:
        attempt += 1
        if max_attempts > 0 and attempt > max_attempts:
            break
        if deadline is not None and time.perf_counter() >= deadline:
            break
        attempt_label = f"{attempt}/{max_attempts}" if max_attempts > 0 else f"{attempt}/unlimited"
        print(f"[image-gen] attempt {attempt_label}", flush=True)
        started = time.perf_counter()
        try:
            request_timeout = timeout
            if deadline is not None:
                request_timeout = max(1, min(timeout, int(deadline - time.perf_counter())))
            response = requests.post(url, headers=headers, json=payload, timeout=request_timeout)
            elapsed = time.perf_counter() - started
            attempt_info = {"attempt": attempt, "status_code": response.status_code, "elapsed_seconds": round(elapsed, 2)}
            try:
                last_data = response.json()
                attempt_info["response_head"] = json.dumps(last_data, ensure_ascii=False)[:1000]
            except Exception:
                last_data = {"raw_text": response.text}
                attempt_info["response_head"] = response.text[:1000]
            attempts.append(attempt_info)
            (output_dir / f"response_attempt_{attempt:02d}.json").write_text(json.dumps(last_data, ensure_ascii=False, indent=2), encoding="utf-8")
            can_retry = (max_attempts <= 0 or attempt < max_attempts) and (deadline is None or time.perf_counter() < deadline)
            if is_resource_insufficient(response, last_data) and can_retry:
                actual_sleep = sleep_seconds if deadline is None else min(sleep_seconds, max(0, deadline - time.perf_counter()))
                print(f"[image-gen] resource insufficient; sleep {actual_sleep:.1f}s then retry", flush=True)
                if actual_sleep > 0:
                    time.sleep(actual_sleep)
                continue
            if response.ok:
                refs = extract_generated_images(last_data if isinstance(last_data, dict) else {})
                if not refs:
                    raise RuntimeError("image generation response has no generated image refs")
                saved: List[str] = []
                postprocess: List[Dict[str, Any]] = []
                for idx, ref in enumerate(refs, 1):
                    suffix = ".png"
                    saved_path = save_generated_image(ref, output_dir / f"generated_{idx:02d}{suffix}")
                    if saved_path:
                        info = fit_image_to_canvas(saved_path, str(payload.get("size", "")))
                        if info:
                            postprocess.append(info)
                        saved.append(str(saved_path))
                return {"ok": True, "attempts": attempts, "response": last_data, "generated_images": refs, "saved_paths": saved, "postprocess": postprocess}
            response.raise_for_status()
        except Exception as exc:
            attempts.append({"attempt": attempt, "exception": type(exc).__name__, "message": str(exc)[:1000]})
            can_retry = (max_attempts <= 0 or attempt < max_attempts) and (deadline is None or time.perf_counter() < deadline)
            if can_retry:
                actual_sleep = sleep_seconds if deadline is None else min(sleep_seconds, max(0, deadline - time.perf_counter()))
                print(f"[image-gen] exception; sleep {actual_sleep:.1f}s then retry: {type(exc).__name__}", flush=True)
                if actual_sleep > 0:
                    time.sleep(actual_sleep)
                continue
            return {"ok": False, "attempts": attempts, "response": last_data, "error": f"{type(exc).__name__}: {exc}"}
    reason = "deadline exceeded" if deadline is not None and time.perf_counter() >= deadline else "max attempts exhausted"
    return {"ok": False, "attempts": attempts, "response": last_data, "error": reason}


def generate_edit_with_retry(
    base_url: str,
    ak: str,
    model: str,
    prompt: str,
    image_paths: List[Path],
    size: str,
    quality: str,
    n: int,
    logid: str,
    max_attempts: int,
    sleep_seconds: int,
    timeout: int,
    output_dir: Path,
    max_seconds: int = 0,
) -> Dict[str, Any]:
    if OpenAI is None:
        raise RuntimeError("openai package is required for SDK image edit mode. Install with: python3 -m pip install --user openai")
    if not image_paths:
        raise ValueError("At least one reference image is required; refusing text-only image generation")
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts: List[Dict[str, Any]] = []
    last_data: Any = None
    started_all = time.perf_counter()
    deadline = started_all + max_seconds if max_seconds and max_seconds > 0 else None
    attempt = 0
    while True:
        attempt += 1
        if max_attempts > 0 and attempt > max_attempts:
            break
        if deadline is not None and time.perf_counter() >= deadline:
            break
        attempt_label = f"{attempt}/{max_attempts}" if max_attempts > 0 else f"{attempt}/unlimited"
        print(f"[image-edit] attempt {attempt_label}", flush=True)
        started = time.perf_counter()
        files = []
        try:
            client = OpenAI(api_key=ak, base_url=base_url, timeout=timeout, max_retries=0)
            files = [open(path, "rb") for path in image_paths]
            result = client.images.edit(
                model=model,
                image=files,
                prompt=prompt,
                n=n,
                size=size,
                quality=quality,
                extra_headers={"X-TT-LOGID": logid} if logid else None,
            )
            elapsed = time.perf_counter() - started
            last_data = result.model_dump() if hasattr(result, "model_dump") else json.loads(result.model_dump_json())
            attempts.append({"attempt": attempt, "status_code": 200, "elapsed_seconds": round(elapsed, 2), "response_head": json.dumps(last_data, ensure_ascii=False)[:1000]})
            (output_dir / f"response_attempt_{attempt:02d}.json").write_text(json.dumps(last_data, ensure_ascii=False, indent=2), encoding="utf-8")
            refs = extract_generated_images(last_data if isinstance(last_data, dict) else {})
            if not refs:
                raise RuntimeError("image edit response has no generated image refs")
            saved: List[str] = []
            postprocess: List[Dict[str, Any]] = []
            for idx, ref in enumerate(refs, 1):
                saved_path = save_generated_image(ref, output_dir / f"generated_{idx:02d}.png")
                if saved_path:
                    info = fit_image_to_canvas(saved_path, size)
                    if info:
                        postprocess.append(info)
                    saved.append(str(saved_path))
            return {"ok": True, "attempts": attempts, "response": last_data, "generated_images": refs, "saved_paths": saved, "postprocess": postprocess}
        except Exception as exc:
            attempts.append({"attempt": attempt, "exception": type(exc).__name__, "message": str(exc)[:1000]})
            can_retry = (max_attempts <= 0 or attempt < max_attempts) and (deadline is None or time.perf_counter() < deadline)
            transient = is_resource_insufficient_payload(str(exc)) or type(exc).__name__ in {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}
            if can_retry and transient:
                actual_sleep = sleep_seconds if deadline is None else min(sleep_seconds, max(0, deadline - time.perf_counter()))
                print(f"[image-edit] transient exception; sleep {actual_sleep:.1f}s then retry: {type(exc).__name__}", flush=True)
                if actual_sleep > 0:
                    time.sleep(actual_sleep)
                continue
            if can_retry:
                # Some gateways return provider capacity as BadRequest text.
                actual_sleep = sleep_seconds if deadline is None else min(sleep_seconds, max(0, deadline - time.perf_counter()))
                print(f"[image-edit] exception; sleep {actual_sleep:.1f}s then retry: {type(exc).__name__}", flush=True)
                if actual_sleep > 0:
                    time.sleep(actual_sleep)
                continue
            return {"ok": False, "attempts": attempts, "response": last_data, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            for f in files:
                try:
                    f.close()
                except Exception:
                    pass
    reason = "deadline exceeded" if deadline is not None and time.perf_counter() >= deadline else "max attempts exhausted"
    return {"ok": False, "attempts": attempts, "response": last_data, "error": reason}


def build_prompt(style: str) -> str:
    style = (style or "ugc_lifestyle").strip()
    base = (
        "Create one TikTok-native ad image. The uploaded reference image is the only source of truth for the product appearance. "
        "Do not describe, redesign, recolor, re-texture, relabel, or reinterpret the product. "
        "Do not add new product variants, labels, logos, patterns, ingredients, materials, shapes, or colors. "
        "Refer to the product only as the referenced product/item, wearing the referenced item, holding the referenced item, using the referenced product, or lower body wearing the item from the reference image. "
        "Do not split this image by product benefit or selling point. If a product benefit is not directly visible in the reference image, ignore it for image generation. "
        "Focus creative detail on the person, pose, crop, camera angle, room/location, lighting, action, expression, and ordinary phone-shot aesthetic. "
        "The referenced product must be clearly visible and visually consistent with the reference. Use an overseas/foreign creator look if a person appears. Prefer no text; no dense text, fake logos, fake claims, fake discounts, or exaggerated results. "
    )
    scene_prompts = {
        "ugc_lifestyle": "Scene type: casual UGC lifestyle photo in a realistic home or daily-life setting, natural daylight, relaxed pose, imperfect phone-shot framing.",
        "routine_scene": "Scene type: daily routine use moment, realistic hands/body action, natural environment, the referenced product naturally worn/held/used.",
        "creator_fit_check": "Scene type: mirror selfie or fit-check when suitable, phone in hand, realistic room, confident but casual pose, crop chosen to show the referenced item clearly.",
        "hands_closeup": "Scene type: close-up use moment, hands or body crop, shallow depth of field, realistic surface/background, the referenced product clearly visible.",
        "outdoor_street": "Scene type: candid outdoor or street-style moment, natural movement, overseas creator, casual phone-shot energy, referenced product visible.",
        "collage_grid": "Scene type: simple TikTok-native collage of two or three real-life scene crops using the same referenced product; no fake product variants or redesigned product.",
        "variant_or_bundle_grid": "Scene type: clean TikTok-native collection or flat-lay image using two to four uploaded reference images as source items. Show only the referenced items together; do not invent extra colors, variants, sizes, bundle components, packaging, labels, logos, or product details.",
        "infographic": "Scene type: clean UGC-style product-in-use scene. Do not add feature labels, arrows, claims, comparison text, or product-detail callouts unless the user prompt explicitly provides exact text.",
    }
    if style in scene_prompts:
        return base + scene_prompts[style]
    return (
        base
        + "Scene type requested by user: "
        + style
        + ". Interpret this only as a visual scene/framing direction. Do not use it to invent product details, benefits, variants, labels, or claims."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate TikTok-ready image creative with reference images and retry")
    parser.add_argument("--reference-image", action="append", required=True, help="Local reference image path; can pass multiple")
    parser.add_argument("--prompt", default="", help="Full prompt. If omitted, --style prompt is used")
    parser.add_argument("--style", default="ugc_lifestyle", help="ugc_lifestyle, infographic, or raw style text")
    parser.add_argument("--output-dir", default="creative_url_video_pipeline/runs/image_gen_test_cellumove")
    parser.add_argument("--endpoint", default=os.environ.get("IMAGE_GEN_ENDPOINT", DEFAULT_EDIT_ENDPOINT))
    parser.add_argument("--base-url", default=os.environ.get("IMAGE_GEN_BASE_URL", ""), help="OpenAI-compatible base URL for SDK image edit mode. Defaults to deriving from --endpoint")
    parser.add_argument("--reference-mode", choices=["sdk_edit", "edit", "generation_image_field"], default=os.environ.get("IMAGE_GEN_REFERENCE_MODE", "sdk_edit"), help="How to send reference images. Default sdk_edit uses OpenAI SDK client.images.edit and consumes image tokens; edit is legacy JSON edit payload; generation_image_field preserves old debug behavior")
    parser.add_argument("--ak", default=os.environ.get("IMAGE_GEN_AK", ""))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--size", default="1152x2048")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=50)
    parser.add_argument("--sleep-seconds", type=int, default=10)
    parser.add_argument("--max-seconds", type=int, default=0, help="Overall retry deadline. Use with --max-attempts 0 for unlimited retries until deadline")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--logid", default=f"codex-image-gen-{int(time.time())}")
    args = parser.parse_args()

    if not args.ak and "ak=" not in args.endpoint:
        raise SystemExit("Missing --ak or IMAGE_GEN_AK")
    ref_paths: List[Path] = []
    for path_s in args.reference_image:
        path = Path(path_s)
        if not path.exists() or not path.is_file():
            raise SystemExit(f"Reference image not found or not a file: {path}")
        ref_paths.append(path)
    if not ref_paths:
        raise SystemExit("At least one reference image is required; refusing text-only image generation")
    effective_endpoint = endpoint_for_reference_mode(args.endpoint, args.reference_mode)
    prompt = args.prompt or build_prompt(args.style)
    payload: Dict[str, Any] = build_reference_payload(args.model, prompt, ref_paths, args.size, args.quality, args.n, args.reference_mode)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    request_meta = {"endpoint": effective_endpoint.split("ak=")[0] + "ak=[REDACTED]" if "ak=" in effective_endpoint else effective_endpoint, "base_url": args.base_url or base_url_from_endpoint(effective_endpoint), "reference_mode": args.reference_mode, "payload": redact_payload(payload)}
    (out_dir / "request_redacted.json").write_text(json.dumps(request_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.reference_mode == "sdk_edit":
        result = generate_edit_with_retry(
            args.base_url or base_url_from_endpoint(effective_endpoint),
            args.ak,
            args.model,
            prompt,
            ref_paths,
            args.size,
            args.quality,
            args.n,
            args.logid,
            max(1, args.max_attempts),
            max(0, args.sleep_seconds),
            args.timeout,
            out_dir,
            max_seconds=max(0, args.max_seconds),
        )
    else:
        result = generate_with_retry(
            effective_endpoint,
            args.ak,
            payload,
            args.logid,
            max(1, args.max_attempts),
            max(0, args.sleep_seconds),
            args.timeout,
            out_dir,
            max_seconds=max(0, args.max_seconds),
        )
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result.get("ok"), "attempt_count": len(result.get("attempts", [])), "saved_paths": result.get("saved_paths", []), "output_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
