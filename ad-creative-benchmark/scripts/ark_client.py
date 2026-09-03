#!/usr/bin/env python3
"""Small Ark image-to-video client used by url_to_ark_video.py.

Secrets are intentionally read from environment variables. Do not hard-code API
keys in this file.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

DEFAULT_ENDPOINT = "https://ark-i18n-tt.tiktok-row.net/api/v3/contents/generations/tasks"
DEFAULT_MODEL = "ep-20260609031613-nqlsf"
TERMINAL_STATUSES = {"succeeded", "success", "completed", "done", "failed", "error", "cancelled", "canceled"}


class ArkHttpError(RuntimeError):
    """HTTP error raised by Ark create/poll calls with machine-readable fields."""

    def __init__(self, phase: str, status_code: int, body: str):
        self.phase = phase
        self.status_code = status_code
        self.body = body
        super().__init__(f"Ark {phase} failed: HTTP {status_code}: {body[:1000]}")


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_task_id(data: Dict[str, Any]) -> Optional[str]:
    for key in ["id", "task_id", "taskId"]:
        if isinstance(data.get(key), str):
            return data[key]
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ["id", "task_id", "taskId"]:
            if isinstance(nested.get(key), str):
                return nested[key]
    return None


def task_status(data: Dict[str, Any]) -> str:
    for key in ["status", "state", "task_status", "phase"]:
        if isinstance(data.get(key), str):
            return data[key].lower()
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ["status", "state", "task_status", "phase"]:
            if isinstance(nested.get(key), str):
                return nested[key].lower()
    return ""


def find_video_urls(obj: Any) -> List[str]:
    urls: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                if isinstance(item, str) and ("http://" in item or "https://" in item):
                    low = item.lower()
                    if any(token in low for token in [".mp4", "video", "tos", "play"]):
                        urls.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(obj)
    out: List[str] = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def build_payload(caption: str, image_urls: List[str], model: str = DEFAULT_MODEL, duration: int = 15, ratio: str = "9:16", generate_audio: bool = True) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [{"type": "text", "text": caption}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    return {
        "model": model,
        "content": content,
        "generate_audio": generate_audio,
        "ratio": ratio,
        "duration": duration,
    }


def submit_task(payload: Dict[str, Any], endpoint: str = DEFAULT_ENDPOINT, api_key: Optional[str] = None, timeout: int = 120) -> Dict[str, Any]:
    api_key = api_key or os.environ.get("ARK_API_KEY", "")
    if not api_key:
        raise RuntimeError("ARK_API_KEY env var is required when submitting Ark tasks")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}
    data["_http_status"] = response.status_code
    if response.status_code >= 400:
        raise ArkHttpError("create", response.status_code, response.text)
    return data


def poll_task(task_id: str, endpoint: str = DEFAULT_ENDPOINT, api_key: Optional[str] = None, output_dir: Optional[Path] = None, prefix: str = "ark", max_polls: int = 45, timeout: int = 120) -> Dict[str, Any]:
    api_key = api_key or os.environ.get("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("ARK_API_KEY env var is required when polling Ark tasks")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    poll_url = f"{endpoint.rstrip('/')}/{task_id}"
    final: Optional[Dict[str, Any]] = None
    for i in range(1, max_polls + 1):
        time.sleep(min(8 + i * 2, 30))
        response = requests.get(poll_url, headers=headers, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = {"raw_text": response.text}
        data["_http_status"] = response.status_code
        if output_dir:
            dump_json(output_dir / f"{prefix}_poll_{i:02d}.json", data)
        if response.status_code >= 400:
            raise ArkHttpError("poll", response.status_code, response.text)
        status = task_status(data)
        urls = find_video_urls(data)
        print(f"Poll {i}: status={status or 'unknown'} video_urls={len(urls)}", flush=True)
        if urls or status in TERMINAL_STATUSES:
            final = data
            break
    if final is None:
        raise TimeoutError(f"Ark task timed out after {max_polls} polls: {task_id}")
    return final


def download_first_video(final_response: Dict[str, Any], output_path: Path, timeout: int = 300) -> Optional[str]:
    urls = find_video_urls(final_response)
    if not urls:
        return None
    url = sorted(urls, key=lambda value: (".mp4" not in value.lower(), len(value)))[0]
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return url
