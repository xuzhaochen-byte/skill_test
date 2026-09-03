#!/usr/bin/env python3
"""Small client for AIDP multimodal crawl chat API.

API keys are read from AIDP_AK_LIST/AIDP_API_KEYS or AIDP_AK/AIDP_API_KEY.
Do not hard-code keys in scripts. The endpoint accepts OpenAI-style messages
with text/image_url/file_url content blocks.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_ENDPOINT = "https://aidp.bytedance.net/api/modelhub/online/multimodal/crawl"
DEFAULT_MODEL = "gemini-2.5-pro"
DEFAULT_MAX_TOKENS = 64000


def split_api_keys(value: Optional[str]) -> List[str]:
    if not value:
        return []
    keys: List[str] = []
    for part in value.replace(";", ",").replace("\n", ",").split(","):
        key = part.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def get_api_keys(explicit: Optional[str] = None) -> List[str]:
    values = [
        explicit,
        os.environ.get("AIDP_AK_LIST"),
        os.environ.get("AIDP_API_KEYS"),
        os.environ.get("AIDP_AK"),
        os.environ.get("AIDP_API_KEY"),
    ]
    keys: List[str] = []
    for value in values:
        for key in split_api_keys(value):
            if key not in keys:
                keys.append(key)
    if not keys:
        raise RuntimeError("AIDP_AK_LIST/AIDP_API_KEYS or AIDP_AK/AIDP_API_KEY env var is required for AIDP provider")
    return keys


def get_api_key(explicit: Optional[str] = None) -> str:
    return get_api_keys(explicit)[0]


def extract_text(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        msg = payload["choices"][0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
            if parts:
                return "\n".join(parts)
    for key in ["content", "text", "answer", "output", "message"]:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return extract_text(data)
    return json.dumps(payload, ensure_ascii=False)


def chat_completion(
    *,
    messages: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    endpoint: str = DEFAULT_ENDPOINT,
    api_key: Optional[str] = None,
    timeout: int = 300,
    logid: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    key = get_api_key(api_key)
    url = endpoint
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("ak", key)
    url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
    body: Dict[str, Any] = {"stream": False, "model": model, "max_tokens": max_tokens, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Content-Type": "application/json"}
    headers["X-TT-LOGID"] = logid or f"codex-{int(time.time() * 1000)}"
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="ignore")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text, "_http_status": resp.status}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:2000]
        raise RuntimeError(f"AIDP HTTP {exc.code}: {detail}") from exc
