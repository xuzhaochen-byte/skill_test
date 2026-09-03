#!/usr/bin/env python3
"""Download a TikTok Creative Studio preview video by vid or preview URL.

This downloader uses the same browser-side flow as the preview page:
1. GET /api/preview/play_auth_token
2. call the VOD GetPlayInfo endpoint with the vid
3. choose a PlayInfo item and download its MainPlayUrl

It does not require internal euler/thrift/idls packages.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional

DEFAULT_BASE_URL = "https://ad-creative-studio-platform.tiktok-row.net"
DEFAULT_VOD_HOST = "https://vod.ap-singapore-1.bytedanceapi.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
VID_RE = re.compile(r"v[0-9a-zA-Z]{20,}")


class DownloadError(RuntimeError):
    pass


def extract_vid(value: str) -> str:
    """Accept either a raw vid or a preview URL containing vid=..."""
    value = value.strip()
    if not value:
        raise DownloadError("empty vid/url")

    parsed = urllib.parse.urlparse(value)
    if parsed.query:
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("vid"):
            return query["vid"][0]

    match = VID_RE.search(value)
    if match:
        return match.group(0)

    raise DownloadError(f"cannot find vid in input: {value!r}")


def http_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise DownloadError(f"HTTP {e.code} for {url}: {body}") from e
    except urllib.error.URLError as e:
        raise DownloadError(f"request failed for {url}: {e}") from e

    try:
        return json.loads(data.decode(charset, "replace"))
    except json.JSONDecodeError as e:
        preview = data[:500].decode("utf-8", "replace")
        raise DownloadError(f"response is not JSON for {url}: {preview}") from e


def get_play_auth_token(base_url: str = DEFAULT_BASE_URL) -> str:
    endpoint = urllib.parse.urljoin(base_url.rstrip("/") + "/", "api/preview/play_auth_token")
    payload = http_json(endpoint)
    if payload.get("code") != 0 or not payload.get("data"):
        raise DownloadError(f"unexpected token response: {payload}")
    return str(payload["data"])


def build_playinfo_url(token_b64: str, vid: str, vod_host: str = DEFAULT_VOD_HOST) -> str:
    try:
        decoded = base64.b64decode(token_b64).decode("utf-8")
        token_obj = json.loads(decoded)
    except Exception as e:
        raise DownloadError(f"cannot decode play auth token: {e}") from e

    signed_query = token_obj.get("GetPlayInfoToken")
    if not signed_query:
        raise DownloadError(f"GetPlayInfoToken missing from token: {token_obj}")

    sep = "&" if "?" in vod_host else "?"
    return f"{vod_host}{sep}{signed_query}&ssl=true&video_id={urllib.parse.quote(vid)}"


def get_playinfo(vid: str, base_url: str = DEFAULT_BASE_URL, vod_host: str = DEFAULT_VOD_HOST) -> Dict[str, Any]:
    token = get_play_auth_token(base_url)
    playinfo_url = build_playinfo_url(token, vid, vod_host)
    payload = http_json(playinfo_url)
    try:
        data = payload["Result"]["Data"]
    except Exception as e:
        raise DownloadError(f"unexpected playinfo response: {payload}") from e
    return data


def definition_number(info: Dict[str, Any]) -> int:
    definition = str(info.get("Definition") or "")
    m = re.search(r"(\d+)", definition)
    if m:
        return int(m.group(1))
    return int(info.get("Height") or 0)


def choose_playinfo(play_list: Iterable[Dict[str, Any]], resolution: str = "best") -> Dict[str, Any]:
    infos = [item for item in play_list if item.get("MainPlayUrl")]
    if not infos:
        raise DownloadError("no playable MainPlayUrl found in PlayInfoList")

    requested = (resolution or "best").strip().lower()
    if requested not in ("", "best", "highest"):
        normalized = requested if requested.endswith("p") else requested + "p"
        matches = [i for i in infos if str(i.get("Definition", "")).lower() == normalized]
        if not matches:
            available = ", ".join(sorted({str(i.get("Definition")) for i in infos}))
            raise DownloadError(f"resolution {normalized} not available; available: {available}")
        return max(matches, key=lambda i: int(i.get("Bitrate") or 0))

    return max(infos, key=lambda i: (definition_number(i), int(i.get("Bitrate") or 0), int(i.get("Size") or 0)))


def safe_filename(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("._") or "video"


def download_file(url: str, output_path: str, timeout: int = 60, overwrite: bool = False) -> None:
    if os.path.exists(output_path) and not overwrite:
        print(f"exists, skip: {output_path}")
        return

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    req = urllib.request.Request(url.replace("http://", "https://"), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(output_path, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_print = 0.0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if total and now - last_print > 0.5:
                    print(f"downloaded {done / total * 100:5.1f}%", end="\r", flush=True)
                    last_print = now
            if total:
                print("downloaded 100.0%")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise DownloadError(f"video download HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise DownloadError(f"video download failed: {e}") from e


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Download Creative Studio preview video by vid or preview URL.")
    parser.add_argument("vid", help="raw vid, or preview URL containing ?vid=...")
    parser.add_argument("-o", "--output", help="output .mp4 path or directory. Default: ./downloads/<vid>.mp4")
    parser.add_argument("-r", "--resolution", default="best", help="best/1080p/720p/540p/etc. Default: best")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"preview site base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--vod-host", default=DEFAULT_VOD_HOST, help=f"VOD API host. Default: {DEFAULT_VOD_HOST}")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing output file")
    args = parser.parse_args(argv)

    try:
        vid = extract_vid(args.vid)
        data = get_playinfo(vid, args.base_url, args.vod_host)
        selected = choose_playinfo(data.get("PlayInfoList") or [], args.resolution)
        output = args.output
        if not output:
            output = os.path.join("downloads", f"{safe_filename(vid)}.mp4")
        elif output.endswith(os.sep) or (os.path.isdir(output) if os.path.exists(output) else not os.path.splitext(output)[1]):
            output = os.path.join(output, f"{safe_filename(vid)}.mp4")

        print(f"vid: {vid}")
        print(
            "selected: {definition} {width}x{height}, bitrate={bitrate}, size={size}".format(
                definition=selected.get("Definition"),
                width=selected.get("Width"),
                height=selected.get("Height"),
                bitrate=selected.get("Bitrate"),
                size=selected.get("Size"),
            )
        )
        download_file(str(selected["MainPlayUrl"]), output, overwrite=args.overwrite)
        print(f"saved: {os.path.abspath(output)}")
        return 0
    except DownloadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
