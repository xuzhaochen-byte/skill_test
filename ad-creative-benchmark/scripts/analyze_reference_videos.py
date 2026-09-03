#!/usr/bin/env python3
"""Analyze downloaded reference videos with a multimodal LLM.

Two providers are supported:

- openai: sample frames with ffmpeg and send image blocks to an
  OpenAI-compatible ARK endpoint.
- aidp: send the whole video as raw base64 bytes to the AIDP multimodal crawl
  endpoint. This path does not require ffmpeg.

Both paths write per-video analysis plus a customer-facing creative
recommendation memo.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aidp_multimodal_client import chat_completion as aidp_chat_completion
from aidp_multimodal_client import extract_text as aidp_extract_text
from aidp_multimodal_client import get_api_keys as aidp_get_api_keys

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

SYSTEM_PROMPT = """你是资深TikTok广告创意策略专家，擅长从高CTR参考视频中拆解可复用的创意方法。
请重点关注前三秒钩子、视觉结构、文案结构、卖点、CTA，以及这些做法如何迁移到当前客户。
所有输出都必须是JSON，不要输出Markdown。"""

VIDEO_PROMPT = """请分析这个高CTR参考视频。你会看到按时间顺序抽帧的画面；如果没有音频转写，请只基于视觉和画面文字，不要编造台词。

当前客户/目标：
- URL: {customer_url}
- 国家: {country}
- 行业: {industry}
- benchmark摘要: {benchmark_summary}

参考素材：
- video_id: {video_id}
- CTR: {ctr}
- rank: {rank}
- external_url: {external_url}
- 与客户落地页相似性: {similarity_score}, 原因: {similarity_reason}

请返回JSON：
{{
  "video_id": "...",
  "first_3_seconds": {{
    "what_is_shown": "前三秒画面在展示什么",
    "likely_message_or_overlay": "可见文字/可能表达，不确定要说明",
    "hook_type": "problem/benefit/curiosity/social-proof/offer/product-demo/other",
    "why_it_may_drive_ctr": "为什么可能提升点击"
  }},
  "creative_structure": [
    {{"stage": "0-3s hook", "description": "..."}},
    {{"stage": "body", "description": "..."}},
    {{"stage": "CTA", "description": "..."}}
  ],
  "selling_points": ["卖点1"],
  "visual_patterns": ["视觉方法1"],
  "copy_patterns": ["文案/口播方法1"],
  "transferable_to_customer": ["适合迁移给客户的具体做法"],
  "not_recommended_to_copy": ["不建议照搬的点"],
  "confidence": 0.0
}}
"""

RECOMMEND_PROMPT = """基于以下客户benchmark和参考视频分析，给客户产出素材制作建议。

客户信息：
{customer_json}

Benchmark摘要：
{benchmark_summary}

参考视频分析JSON：
{video_analyses}

请返回JSON：
{{
  "strategy_summary": "总体素材策略，一段话",
  "priority_opportunities": ["优先机会"],
  "creative_angles": [
    {{
      "angle_name": "角度名称",
      "why": "为什么适合当前客户/benchmark短板",
      "first_3s_script": "前三秒建议画面+字幕/口播",
      "storyboard": ["镜头1", "镜头2", "镜头3"],
      "selling_points": ["卖点"],
      "cta": "CTA建议"
    }}
  ],
  "production_checklist": ["拍摄/剪辑执行清单"],
  "testing_plan": ["A/B测试建议"],
  "risks": ["注意事项"]
}}
"""


def parse_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def load_sample_defaults(sample_py: Path) -> Tuple[str, str]:
    model = "ep-20260224144418-l7d6b"
    base_url = "https://ark-cn-beijing.bytedance.net/api/v3"
    if sample_py.exists():
        text = sample_py.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'--model"[^\n]*default="([^"]+)"', text)
        if m:
            model = m.group(1)
        m = re.search(r'base_url="([^"]+)"', text)
        if m:
            base_url = m.group(1)
    return model, base_url


def extract_json(text: str) -> Dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"LLM did not return JSON: {text[:400]}")
    return json.loads(text[start:end])


def ffmpeg_available() -> bool:
    return subprocess.run(["bash", "-lc", "command -v ffmpeg >/dev/null 2>&1"]).returncode == 0


def sample_frames(video_path: Path, output_dir: Path, fps: float, max_frames: int, width: int) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%03d.jpg")
    vf = f"fps={fps},scale={width}:-1"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-vf", vf, "-frames:v", str(max_frames), pattern]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {video_path}: {proc.stderr}")
    return sorted(output_dir.glob("frame_*.jpg"))


def image_block(path: Path) -> Dict[str, Any]:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def video_file_block(path: Path) -> Dict[str, Any]:
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    # AIDP maps file_url + mime_type to Gemini inline_data. In that mode the
    # field must be raw base64 bytes, not a data: URL prefix; otherwise Gemini
    # returns "Base64 decoding failed".
    return {"type": "file_url", "file_url": {"mime_type": mime, "url": data}}


def call_llm_json(client: Any, model: str, messages: List[Dict[str, Any]], max_tokens: int = 1800) -> Dict[str, Any]:
    last_error = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, temperature=0.2, max_tokens=max_tokens)
            return extract_json(resp.choices[0].message.content.strip())
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed: {last_error}")


def call_aidp_json(model: str, messages: List[Dict[str, Any]], max_tokens: int, timeout: int, api_keys: List[str], retries: int) -> Dict[str, Any]:
    last_error = None
    total_attempts = max(1, retries, len(api_keys))
    for attempt in range(total_attempts):
        api_key = api_keys[attempt % len(api_keys)]
        try:
            payload = aidp_chat_completion(model=model, max_tokens=max_tokens, messages=messages, timeout=timeout, temperature=0.2, api_key=api_key)
            return extract_json(aidp_extract_text(payload))
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"AIDP call failed after {total_attempts} attempts across {len(api_keys)} key(s): {last_error}")


def load_benchmark_summary(path: Optional[Path]) -> Tuple[Dict[str, Any], str]:
    if not path:
        return {}, ""
    data = json.loads(path.read_text(encoding="utf-8"))
    water = data.get("waterline", {})
    parts = []
    for key in ["ctr", "cvr", "cost", "play_3s_ratio"]:
        item = water.get(key) or {}
        if item:
            parts.append(f"{item.get('label', key)}={item.get('formatted_value')} / {item.get('band')} / score {item.get('score')}: {item.get('interpretation')}")
    summary = "\n".join(parts + [*(data.get("summary", {}).get("recommendations") or [])])
    customer = {
        "url": data.get("input", {}).get("url"),
        "country": data.get("input", {}).get("country"),
        "industry": data.get("industry_classification", {}).get("industry") or data.get("benchmark", {}).get("industry"),
    }
    return {"customer": customer, "benchmark": data}, summary


def load_manifest(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    videos = data.get("videos") if isinstance(data, dict) else data
    if not isinstance(videos, list):
        raise ValueError(f"No videos list found in {path}")
    return [dict(v) for v in videos if dict(v).get("status") == "ok" and Path(str(dict(v).get("path", ""))).exists()]


def analyze_video(client: Any, model: str, video: Dict[str, Any], customer: Dict[str, Any], benchmark_summary: str, args: argparse.Namespace, frames_root: Path) -> Dict[str, Any]:
    video_path = Path(str(video["path"]))
    frame_dir = frames_root / str(video.get("video_id") or video_path.stem)
    frames = sample_frames(video_path, frame_dir, args.frame_fps, args.max_frames, args.frame_width)
    if not frames:
        raise RuntimeError(f"No frames sampled from {video_path}")
    user_text = VIDEO_PROMPT.format(
        customer_url=customer.get("url", ""),
        country=customer.get("country", ""),
        industry=customer.get("industry", ""),
        benchmark_summary=benchmark_summary[:2000],
        video_id=video.get("video_id", ""),
        ctr=video.get("ctr", ""),
        rank=video.get("rank", ""),
        external_url=video.get("external_url", ""),
        similarity_score=video.get("similarity_score", ""),
        similarity_reason=video.get("similarity_reason", ""),
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    for i, frame in enumerate(frames, 1):
        content.append({"type": "text", "text": f"Frame {i}/{len(frames)} sampled in chronological order"})
        content.append(image_block(frame))
    result = call_llm_json(client, model, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}])
    result.setdefault("video_id", video.get("video_id"))
    result["source"] = {k: video.get(k) for k in ["video_id", "rank", "ctr", "external_url", "preview_url", "similarity_score", "path"]}
    result["sampled_frames"] = [str(p) for p in frames]
    return result


def analyze_video_aidp(model: str, video: Dict[str, Any], customer: Dict[str, Any], benchmark_summary: str, args: argparse.Namespace) -> Dict[str, Any]:
    video_path = Path(str(video["path"]))
    user_text = VIDEO_PROMPT.format(
        customer_url=customer.get("url", ""),
        country=customer.get("country", ""),
        industry=customer.get("industry", ""),
        benchmark_summary=benchmark_summary[:4000],
        video_id=video.get("video_id", ""),
        ctr=video.get("ctr", ""),
        rank=video.get("rank", ""),
        external_url=video.get("external_url", ""),
        similarity_score=video.get("similarity_score", ""),
        similarity_reason=video.get("similarity_reason", ""),
    )
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": user_text + "\n\n请直接分析整个视频文件，不需要抽帧。"},
        video_file_block(video_path),
    ]
    result = call_aidp_json(
        model,
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
        args.aidp_max_tokens,
        args.aidp_timeout,
        args.aidp_api_keys,
        args.aidp_retries,
    )
    result.setdefault("video_id", video.get("video_id"))
    result["source"] = {k: video.get(k) for k in ["video_id", "rank", "ctr", "external_url", "preview_url", "similarity_score", "path"]}
    result["video_input"] = {"type": "full_video_base64", "path": str(video_path), "size_bytes": video_path.stat().st_size}
    return result


def analyze_video_worker(base_url: str, api_key: str, model: str, video: Dict[str, Any], customer: Dict[str, Any], benchmark_summary: str, args: argparse.Namespace, frames_root: Path) -> Dict[str, Any]:
    """Create a per-thread client and analyze one video.

    Keeping the OpenAI client local to the worker avoids relying on client
    thread-safety while still parallelizing the expensive ffmpeg + MLLM calls.
    """
    client = OpenAI(base_url=base_url, api_key=api_key)
    return analyze_video(client, model, video, customer, benchmark_summary, args, frames_root)


def analyze_video_aidp_worker(model: str, video: Dict[str, Any], customer: Dict[str, Any], benchmark_summary: str, args: argparse.Namespace) -> Dict[str, Any]:
    return analyze_video_aidp(model, video, customer, benchmark_summary, args)


def write_markdown_recommendations(result: Dict[str, Any], output: Path) -> None:
    lines = ["# Creative Recommendations", ""]
    if result.get("strategy_summary"):
        lines += ["## Strategy Summary", str(result["strategy_summary"]), ""]
    if result.get("priority_opportunities"):
        lines += ["## Priority Opportunities", *[f"- {x}" for x in result["priority_opportunities"]], ""]
    if result.get("creative_angles"):
        lines.append("## Creative Angles")
        for angle in result["creative_angles"]:
            lines += [f"### {angle.get('angle_name', 'Angle')}", f"- Why: {angle.get('why', '')}", f"- First 3s: {angle.get('first_3s_script', '')}"]
            if angle.get("storyboard"):
                lines += ["- Storyboard:", *[f"  - {x}" for x in angle.get("storyboard", [])]]
            if angle.get("selling_points"):
                lines += ["- Selling points: " + "; ".join(angle.get("selling_points", []))]
            lines += [f"- CTA: {angle.get('cta', '')}", ""]
    for section, title in [("production_checklist", "Production Checklist"), ("testing_plan", "Testing Plan"), ("risks", "Risks")]:
        if result.get(section):
            lines += [f"## {title}", *[f"- {x}" for x in result[section]], ""]
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze downloaded reference videos and generate creative recommendations.")
    parser.add_argument("--download-manifest", type=Path, required=True)
    parser.add_argument("--benchmark-result", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-py", type=Path, default=Path("sample.py"))
    parser.add_argument("--model", default=None, help="Use a multimodal Ark model/endpoint. Defaults to sample.py model, which may be text-only.")
    parser.add_argument("--provider", choices=["openai", "aidp"], default="openai", help="aidp sends the full video as raw base64 file_url bytes to the multimodal crawl endpoint and does not require ffmpeg.")
    parser.add_argument("--aidp-max-tokens", type=int, default=64000)
    parser.add_argument("--aidp-timeout", type=int, default=600)
    parser.add_argument("--aidp-retries", type=int, default=3, help="AIDP retry attempts. If multiple keys are configured, attempts rotate across keys.")
    parser.add_argument("--max-videos", type=int, default=5)
    parser.add_argument("--frame-fps", type=float, default=0.5, help="Frames per second to sample")
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--frame-width", type=int, default=512)
    parser.add_argument("--workers", type=int, default=3, help="Parallel video-analysis workers")
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()

    if args.provider == "openai" and OpenAI is None:
        raise RuntimeError("openai package unavailable; cannot call sample.py-compatible LLM")
    if args.provider == "openai" and not os.environ.get("ARK_API_KEY"):
        raise RuntimeError("ARK_API_KEY not set; required for multimodal LLM analysis")
    if args.provider == "aidp":
        args.aidp_api_keys = aidp_get_api_keys()
    else:
        args.aidp_api_keys = []
    if args.provider == "openai" and not ffmpeg_available():
        raise RuntimeError("ffmpeg is required for frame sampling but was not found")

    if args.provider == "aidp":
        base_url = ""
        api_key = ""
        client = None
        model = args.model or "gemini-2.5-pro"
    else:
        default_model, base_url = load_sample_defaults(args.sample_py)
        model = args.model or default_model
        api_key = os.environ["ARK_API_KEY"]
        client = OpenAI(base_url=base_url, api_key=api_key)
    loaded, benchmark_summary = load_benchmark_summary(args.benchmark_result)
    customer = loaded.get("customer", {})
    videos = load_manifest(args.download_manifest)[: args.max_videos]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frames_root = args.output_dir / "sampled_frames"

    analyses: List[Dict[str, Any]] = []
    if not videos:
        print("No downloaded videos found in manifest; writing empty analysis.", file=sys.stderr)
    else:
        workers = max(1, min(args.workers, len(videos)))
        if args.provider == "aidp" and workers == 1:
            for i, video in enumerate(videos, 1):
                print(f"[{i}/{len(videos)}] analyzing full video via AIDP {video.get('video_id')}", file=sys.stderr)
                analyses.append(analyze_video_aidp(model, video, customer, benchmark_summary, args))
        elif workers == 1:
            for i, video in enumerate(videos, 1):
                print(f"[{i}/{len(videos)}] analyzing {video.get('video_id')}", file=sys.stderr)
                analyses.append(analyze_video(client, model, video, customer, benchmark_summary, args, frames_root))
        else:
            ordered: List[Optional[Dict[str, Any]]] = [None] * len(videos)
            print(f"Analyzing {len(videos)} videos with {workers} workers", file=sys.stderr)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                if args.provider == "aidp":
                    futures = {
                        pool.submit(analyze_video_aidp_worker, model, video, customer, benchmark_summary, args): (idx, video)
                        for idx, video in enumerate(videos)
                    }
                else:
                    futures = {
                        pool.submit(analyze_video_worker, base_url, api_key, model, video, customer, benchmark_summary, args, frames_root): (idx, video)
                        for idx, video in enumerate(videos)
                    }
                completed = 0
                for fut in as_completed(futures):
                    idx, video = futures[fut]
                    completed += 1
                    vid = video.get("video_id")
                    try:
                        ordered[idx] = fut.result()
                        print(f"[{completed}/{len(videos)}] analyzed {vid}", file=sys.stderr)
                    except Exception as exc:
                        print(f"[{completed}/{len(videos)}] failed {vid}: {exc}", file=sys.stderr)
                        ordered[idx] = {
                            "video_id": vid,
                            "error": str(exc),
                            "source": {k: video.get(k) for k in ["video_id", "rank", "ctr", "external_url", "preview_url", "similarity_score", "path"]},
                        }
            analyses = [x for x in ordered if x is not None]

    customer_json = json.dumps(customer, ensure_ascii=False, indent=2)
    rec_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": RECOMMEND_PROMPT.format(
            customer_json=customer_json,
            benchmark_summary=benchmark_summary[:2500],
            video_analyses=json.dumps(analyses, ensure_ascii=False, indent=2)[:12000],
        )},
    ]
    if args.provider == "aidp":
        rec = call_aidp_json(model, rec_messages, args.aidp_max_tokens, args.aidp_timeout, args.aidp_api_keys, args.aidp_retries)
    else:
        rec = call_llm_json(client, model, rec_messages, max_tokens=2200)
    output = {"customer": customer, "benchmark_summary": benchmark_summary, "video_count": len(analyses), "video_analyses": analyses, "recommendations": rec}
    json_path = args.output_dir / "video_analysis.json"
    md_path = args.output_dir / "creative_recommendations.md"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_recommendations(rec, md_path)
    if not args.keep_frames:
        # Keep by default? We remove only if empty cleanup is easy; frame paths in JSON are useful, so leave frames.
        pass
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "videos_analyzed": len(analyses)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
