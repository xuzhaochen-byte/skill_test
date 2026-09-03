#!/usr/bin/env python3
"""Filter CTR Top reference creatives by landing-page/product similarity.

Inputs are the customer benchmark_result.json and ctr_top50_videos.json/csv from
Aeolus. The script fetches lightweight landing-page text when possible and asks
sample.py-compatible LLM to judge whether each Top creative's destination is a
usable reference for the customer.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from aidp_multimodal_client import chat_completion as aidp_chat_completion
from aidp_multimodal_client import extract_text as aidp_extract_text
from aidp_multimodal_client import get_api_keys as aidp_get_api_keys

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

SYSTEM_PROMPT = """你是广告素材策略分析师。你的任务是判断两个落地页/商品是否适合作为素材参考。
只返回JSON。不要因为同属大行业就判定相似，必须关注商品品类、购买场景、目标人群、价位/品牌定位和转化路径。"""

USER_PROMPT = """请判断候选Top CTR素材的落地页，是否适合给当前客户作为素材参考。

当前客户：
- URL: {customer_url}
- 国家: {country}
- 行业: {industry}
- 落地页文本摘要: {customer_text}

候选素材：
- rank: {rank}
- video_id: {video_id}
- external_url: {external_url}
- final_url: {final_url}
- 数据集细分字段: {candidate_context}
- CTR: {ctr}
- Impressions: {impressions}
- Clicks: {clicks}
- 候选落地页文本摘要: {candidate_text}
- 抓取状态: {fetch_status} {fetch_error}

判定标准：
1. 商品/服务是否相同或高度近似；例如婚礼服饰/内衣/塑身衣，与普通鞋服或无关商品不能只因同属Clothing就判相似。
2. 使用场景和购买意图是否相近；例如wedding shop、bridal、occasion wear、shapewear/underwear等。
3. 目标人群、价格/品牌定位、销售渠道是否可迁移。
4. 如果候选落地页无法抓取且URL没有足够商品信息，给低置信，不要强行判相似；但如果URL/domain/path、广告主/品牌名或数据集细分字段已经清楚表明是同一细分品类/竞品，可以给>=0.65。
5. 优先使用通用数据集字段判断：External URL Domains、Advertiser Name、Brand Name、Account Industry V40各层级、AIC Category、Product Source、Catalog Type。不要依赖某个固定品牌或固定品类关键词。

返回JSON，不要多余文字：
{{
  "is_similar": true,
  "similarity_score": 0.0,
  "same_product_category": true,
  "same_purchase_context": true,
  "confidence": 0.0,
  "reason": "一句话说明为什么适合或不适合",
  "transferable_points": ["可迁移的点"],
  "risk": "使用该素材做参考的风险"
}}
"""


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
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
        raise ValueError(f"LLM did not return JSON: {text[:300]}")
    return json.loads(text[start:end])


def fetch_page_text(url: str, timeout: int = 15, max_bytes: int = 1_200_000) -> Dict[str, Any]:
    url = (url or "").strip()
    if not url:
        return {"status": "missing_url", "text": "", "final_url": "", "error": "URL is empty"}
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8,ja;q=0.7",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            final_url = resp.geturl()
            content_type = resp.headers.get("content-type", "")
    except Exception as exc:
        return {"status": "fetch_failed", "text": "", "final_url": "", "error": str(exc)}

    enc = "utf-8"
    m = re.search(r"charset=([^;]+)", content_type, flags=re.I)
    if m:
        enc = m.group(1).strip()
    try:
        doc = raw.decode(enc, errors="ignore")
    except LookupError:
        doc = raw.decode("utf-8", errors="ignore")

    title = ""
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", doc)
    if title_m:
        title = html.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip()
    metas = []
    for mm in re.finditer(r"(?is)<meta\s+[^>]*(?:name|property)=['\"](?:description|og:title|og:description)['\"][^>]*>", doc):
        cm = re.search(r"(?is)content=['\"](.*?)['\"]", mm.group(0))
        if cm:
            metas.append(html.unescape(cm.group(1)).strip())
    doc = re.sub(r"(?is)<script\b.*?</script>", " ", doc)
    doc = re.sub(r"(?is)<style\b.*?</style>", " ", doc)
    text = re.sub(r"(?s)<[^>]+>", " ", doc)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    combined = "\n".join([x for x in [title, *metas, text] if x]).strip()
    return {"status": "ok", "text": combined[:5000], "final_url": final_url, "error": "", "content_type": content_type}


def normalize_domain(value: Any) -> str:
    """Normalize a URL or host for same-customer exclusion.

    This intentionally uses hostname-level matching rather than product keywords:
    `www.example.com` and `example.com` are treated as the same domain, while
    unrelated hosts remain eligible for LLM similarity review.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        host = urlparse(text).hostname or ""
    except Exception:
        host = ""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def candidate_domains(row: Dict[str, Any], final_url: str = "") -> List[str]:
    domains: List[str] = []
    for value in [row.get("External Website URL"), row.get("external_url"), row.get("Video URL"), final_url]:
        domain = normalize_domain(value)
        if domain and domain not in domains:
            domains.append(domain)
    # Aeolus may provide one or more domains in a plain-text context field.
    raw_domains = str(row.get("External URL Domains") or "")
    for part in re.split(r"[,;\s]+", raw_domains):
        domain = normalize_domain(part)
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def is_same_customer_domain(customer_domain: str, row: Dict[str, Any], final_url: str = "") -> bool:
    if not customer_domain:
        return False
    return customer_domain in candidate_domains(row, final_url)


def read_top_rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return list(data.get("rows") or [])
        if isinstance(data, list):
            return data
        raise ValueError(f"Unsupported JSON shape in {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_customer(benchmark_result: Path, customer_url: Optional[str]) -> Dict[str, Any]:
    data = json.loads(benchmark_result.read_text(encoding="utf-8"))
    ctx = data.get("adv_context") or {}
    if ctx.get("source"):
        try:
            source = Path(ctx["source"])
            if not source.is_absolute():
                source = Path.cwd() / source
            selected = (json.loads(source.read_text(encoding="utf-8")).get("selected") or {})
            ctx = {**selected, **ctx}
        except Exception:
            pass
    return {
        "url": customer_url or data.get("input", {}).get("url", ""),
        "country": data.get("input", {}).get("country", ""),
        "industry": data.get("industry_classification", {}).get("industry") or data.get("benchmark", {}).get("industry", ""),
        "text": data.get("landing_page", {}).get("text_excerpt", ""),
        "context": ctx,
        "benchmark_result": data,
    }


def heuristic_similarity(customer: Dict[str, Any], candidate: Dict[str, Any], reason: str) -> Dict[str, Any]:
    hay = " ".join([customer.get("url", ""), customer.get("text", "")]).lower()
    cand = " ".join([candidate.get("External Website URL", ""), candidate.get("_landing_text", "")]).lower()
    product_terms = ["wedding", "bridal", "bride", "shapewear", "underwear", "lingerie", "dress", "skims"]
    matches = [t for t in product_terms if t in hay and t in cand]
    score = min(0.75, 0.2 + 0.15 * len(matches)) if matches else 0.2
    return {
        "is_similar": score >= 0.65,
        "similarity_score": round(score, 2),
        "same_product_category": bool(matches),
        "same_purchase_context": any(t in matches for t in ["wedding", "bridal", "bride"]),
        "confidence": 0.25,
        "reason": f"Heuristic fallback used because {reason}. Matched terms: {', '.join(matches) or 'none'}.",
        "transferable_points": [],
        "risk": "Low-confidence fallback; use LLM result before business decisions.",
    }


def call_llm(client: Any, model: str, customer: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    context_keys = [
        "External URL Domains", "Advertiser Name", "Brand Name (Latest)",
        "Account Industry Level 0 Name V40 (Latest)", "Account Industry Level 1 Name V40 (Latest)",
        "Account Industry Level 2 Name V40 (Latest)", "Account Industry Level 3 Name V40 (Latest)",
        "First AIC Category Name", "Second AIC Category Name", "Third AIC Category Name",
        "Product Source", "Catalog Type",
    ]
    candidate_context = {k: row.get(k) for k in context_keys if str(row.get(k) or "").strip()}
    customer_context = customer.get("context") or {}
    customer_text = (customer.get("text", "") or "")[:2500] or "[unavailable]"
    useful_customer_context = {k: customer_context.get(k) for k in context_keys if str(customer_context.get(k) or "").strip()}
    if useful_customer_context:
        customer_text = f"数据集细分字段: {json.dumps(useful_customer_context, ensure_ascii=False)}\n页面摘要: {customer_text}"
    msg = USER_PROMPT.format(
        customer_url=customer.get("url", ""),
        country=customer.get("country", ""),
        industry=customer.get("industry", ""),
        customer_text=customer_text,
        rank=row.get("rank", ""),
        video_id=row.get("Video ID", ""),
        external_url=row.get("External Website URL", ""),
        final_url=row.get("_final_url", ""),
        candidate_context=json.dumps(candidate_context, ensure_ascii=False) if candidate_context else "{}",
        ctr=row.get("CTR", ""),
        impressions=row.get("Impressions", ""),
        clicks=row.get("Clicks (Destination)", ""),
        candidate_text=(row.get("_landing_text", "") or "")[:2500] or "[unavailable]",
        fetch_status=row.get("_fetch_status", ""),
        fetch_error=row.get("_fetch_error", ""),
    )
    last_error = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": msg}],
                temperature=0,
                max_tokens=700,
            )
            result = extract_json(resp.choices[0].message.content.strip())
            return normalize_similarity(result)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM similarity failed for {row.get('Video ID')}: {last_error}")


def call_aidp_llm(model: str, customer: Dict[str, Any], row: Dict[str, Any], max_tokens: int, timeout: int, api_keys: List[str], retries: int) -> Dict[str, Any]:
    context_keys = [
        "External URL Domains", "Advertiser Name", "Brand Name (Latest)",
        "Account Industry Level 0 Name V40 (Latest)", "Account Industry Level 1 Name V40 (Latest)",
        "Account Industry Level 2 Name V40 (Latest)", "Account Industry Level 3 Name V40 (Latest)",
        "First AIC Category Name", "Second AIC Category Name", "Third AIC Category Name",
        "Product Source", "Catalog Type",
    ]
    candidate_context = {k: row.get(k) for k in context_keys if str(row.get(k) or "").strip()}
    customer_context = customer.get("context") or {}
    customer_text = (customer.get("text", "") or "")[:2500] or "[unavailable]"
    useful_customer_context = {k: customer_context.get(k) for k in context_keys if str(customer_context.get(k) or "").strip()}
    if useful_customer_context:
        customer_text = f"数据集细分字段: {json.dumps(useful_customer_context, ensure_ascii=False)}\n页面摘要: {customer_text}"
    msg = USER_PROMPT.format(
        customer_url=customer.get("url", ""),
        country=customer.get("country", ""),
        industry=customer.get("industry", ""),
        customer_text=customer_text,
        rank=row.get("rank", ""),
        video_id=row.get("Video ID", ""),
        external_url=row.get("External Website URL", ""),
        final_url=row.get("_final_url", ""),
        candidate_context=json.dumps(candidate_context, ensure_ascii=False) if candidate_context else "{}",
        ctr=row.get("CTR", ""),
        impressions=row.get("Impressions", ""),
        clicks=row.get("Clicks (Destination)", ""),
        candidate_text=(row.get("_landing_text", "") or "")[:2500] or "[unavailable]",
        fetch_status=row.get("_fetch_status", ""),
        fetch_error=row.get("_fetch_error", ""),
    )
    last_error = None
    total_attempts = max(1, retries, len(api_keys))
    for attempt in range(total_attempts):
        api_key = api_keys[attempt % len(api_keys)]
        try:
            payload = aidp_chat_completion(
                model=model,
                max_tokens=max_tokens,
                timeout=timeout,
                api_key=api_key,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [{"type": "text", "text": msg}]},
                ],
                temperature=0,
            )
            return normalize_similarity(extract_json(aidp_extract_text(payload)))
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"AIDP similarity failed for {row.get('Video ID')} after {total_attempts} attempts across {len(api_keys)} key(s): {last_error}")


def normalize_similarity(result: Dict[str, Any]) -> Dict[str, Any]:
    score = parse_float(result.get("similarity_score"))
    conf = parse_float(result.get("confidence"))
    score = 0.0 if score is None else max(0.0, min(1.0, score))
    conf = 0.0 if conf is None else max(0.0, min(1.0, conf))
    return {
        "is_similar": bool(result.get("is_similar")) and score >= 0.5,
        "similarity_score": round(score, 3),
        "same_product_category": bool(result.get("same_product_category")),
        "same_purchase_context": bool(result.get("same_purchase_context")),
        "confidence": round(conf, 3),
        "reason": str(result.get("reason", "")),
        "transferable_points": [str(x) for x in (result.get("transferable_points") or [])][:5],
        "risk": str(result.get("risk", "")),
    }


def write_outputs(rows: List[Dict[str, Any]], output_dir: Path, threshold: float, customer: Dict[str, Any], excluded_same_domain_count: int = 0) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    accepted = [r for r in rows if r.get("is_similar") and (parse_float(r.get("similarity_score")) or 0) >= threshold]
    payload = {
        "customer": {"url": customer.get("url"), "country": customer.get("country"), "industry": customer.get("industry")},
        "threshold": threshold,
        "row_count": len(rows),
        "accepted_count": len(accepted),
        "excluded_same_domain_count": excluded_same_domain_count,
        "rows": rows,
    }
    json_path = output_dir / "similar_landing_pages.json"
    csv_path = output_dir / "similar_landing_pages.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "rank", "Video ID", "External Website URL", "Video URL", "CTR", "Impressions", "Clicks (Destination)",
        "is_similar", "similarity_score", "same_product_category", "same_purchase_context", "confidence",
        "reason", "transferable_points", "risk", "_fetch_status", "_final_url", "_fetch_error",
        "_excluded_reason", "_customer_domain", "_candidate_domains",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["transferable_points"] = "; ".join(out.get("transferable_points") or [])
            writer.writerow(out)
    return json_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter CTR Top videos by landing-page similarity to the customer.")
    parser.add_argument("--benchmark-result", type=Path, required=True)
    parser.add_argument("--top-videos", type=Path, required=True, help="ctr_top50_videos.json or .csv")
    parser.add_argument("--customer-url", default=None)
    parser.add_argument("--sample-py", type=Path, default=Path("sample.py"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=["openai", "aidp"], default="openai", help="LLM provider for similarity. aidp uses AIDP_AK/AIDP_API_KEY and the multimodal crawl endpoint.")
    parser.add_argument("--aidp-max-tokens", type=int, default=64000)
    parser.add_argument("--aidp-timeout", type=int, default=300)
    parser.add_argument("--aidp-retries", type=int, default=3, help="AIDP retry attempts. If multiple keys are configured, attempts rotate across keys.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--min-threshold", type=float, default=0.45, help="Auto-relax floor: when zero references pass --threshold, accept similar candidates down to this score so the report is not left without reference creatives.")
    parser.add_argument("--no-auto-relax", action="store_true", help="Disable the automatic threshold relaxation that prevents an empty reference set.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--no-fetch", action="store_true", help="Do not fetch landing pages; classify from URLs and benchmark excerpt only.")
    parser.add_argument("--fetch-customer", action="store_true", help="Refetch customer landing page text instead of only using benchmark excerpt.")
    parser.add_argument("--allow-heuristic-similarity", action="store_true", help="Local smoke-test fallback when ARK_API_KEY/LLM is unavailable.")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers for fetching and LLM similarity. Default: 8")
    parser.add_argument("--allow-same-domain", action="store_true", help="Do not exclude candidates whose external URL domain matches the customer domain. Use only for debugging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    customer = load_customer(args.benchmark_result, args.customer_url)
    if args.fetch_customer and not args.no_fetch and customer.get("url"):
        fetched = fetch_page_text(customer["url"])
        if fetched.get("text"):
            customer["text"] = fetched["text"]
        customer["fetch"] = fetched

    rows = read_top_rows(args.top_videos)[: args.limit]
    customer_domain = normalize_domain(customer.get("url"))
    if args.provider == "openai" and OpenAI is None and not args.allow_heuristic_similarity:
        raise RuntimeError("openai package unavailable; cannot call sample.py-compatible LLM")
    api_key = os.environ.get("ARK_API_KEY")
    client = None
    model = args.model
    aidp_api_keys: List[str] = []
    if args.provider == "aidp":
        model = model or "gemini-2.5-pro"
        if not args.allow_heuristic_similarity:
            aidp_api_keys = aidp_get_api_keys()
    elif api_key and OpenAI is not None:
        default_model, base_url = load_sample_defaults(args.sample_py)
        model = model or default_model
        client = OpenAI(base_url=base_url, api_key=api_key)
    elif not args.allow_heuristic_similarity:
        raise RuntimeError("ARK_API_KEY not set; required for LLM similarity. Use --allow-heuristic-similarity only for smoke tests.")

    def analyze_one(item: Tuple[int, Dict[str, Any]]) -> Dict[str, Any]:
        index, original = item
        row = dict(original)
        row.setdefault("rank", index)
        url = row.get("External Website URL") or ""
        row["_customer_domain"] = customer_domain
        row["_candidate_domains"] = "; ".join(candidate_domains(row))
        if not args.allow_same_domain and is_same_customer_domain(customer_domain, row):
            row.update({
                "_fetch_status": "skipped_same_domain",
                "_landing_text": "",
                "_final_url": "",
                "_fetch_error": "",
                "_excluded_reason": "candidate_external_url_domain_matches_customer_domain",
                "is_similar": False,
                "similarity_score": 0.0,
                "same_product_category": False,
                "same_purchase_context": False,
                "confidence": 1.0,
                "reason": "Excluded before LLM review because the candidate landing-page domain matches the customer's domain; likely the same advertiser/customer rather than an external reference.",
                "transferable_points": [],
                "risk": "Same-domain references are excluded to avoid recommending the customer's own materials as benchmarks.",
            })
            return row
        if args.no_fetch:
            fetched = {"status": "skipped", "text": "", "final_url": "", "error": ""}
        else:
            fetched = fetch_page_text(url)
        if not args.allow_same_domain and is_same_customer_domain(customer_domain, row, fetched.get("final_url", "")):
            row["_candidate_domains"] = "; ".join(candidate_domains(row, fetched.get("final_url", "")))
            row.update({
                "_fetch_status": "skipped_same_domain",
                "_landing_text": "",
                "_final_url": fetched.get("final_url", ""),
                "_fetch_error": "",
                "_excluded_reason": "candidate_final_url_domain_matches_customer_domain",
                "is_similar": False,
                "similarity_score": 0.0,
                "same_product_category": False,
                "same_purchase_context": False,
                "confidence": 1.0,
                "reason": "Excluded before LLM review because the fetched final landing-page domain matches the customer's domain; likely the same advertiser/customer rather than an external reference.",
                "transferable_points": [],
                "risk": "Same-domain references are excluded to avoid recommending the customer's own materials as benchmarks.",
            })
            return row
        row["_fetch_status"] = fetched.get("status", "")
        row["_landing_text"] = fetched.get("text", "")
        row["_final_url"] = fetched.get("final_url", "")
        row["_fetch_error"] = fetched.get("error", "")
        if args.provider == "aidp" and aidp_api_keys:
            sim = call_aidp_llm(str(model), customer, row, args.aidp_max_tokens, args.aidp_timeout, aidp_api_keys, args.aidp_retries)
        elif client is not None:
            sim = call_llm(client, str(model), customer, row)
        else:
            sim = heuristic_similarity(customer, row, "ARK_API_KEY not set or OpenAI unavailable")
        row.update(sim)
        return row

    indexed_rows = list(enumerate(rows, 1))
    analyzed: List[Dict[str, Any]] = []
    workers = max(1, int(args.workers or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(analyze_one, item): item[0] for item in indexed_rows}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            row = future.result()
            analyzed.append(row)
            print(f"[{completed}/{len(rows)}] rank={row.get('rank')} {row.get('Video ID')} score={row.get('similarity_score')} similar={row.get('is_similar')}", file=sys.stderr)

    excluded_same_domain_count = sum(1 for r in analyzed if str(r.get("_excluded_reason") or "").startswith("candidate_"))
    analyzed.sort(key=lambda r: (not bool(r.get("is_similar")), bool(r.get("_excluded_reason")), -(parse_float(r.get("similarity_score")) or 0), int(parse_float(r.get("rank")) or 999999)))

    def count_accepted(th: float) -> int:
        return sum(1 for r in analyzed if r.get("is_similar") and (parse_float(r.get("similarity_score")) or 0) >= th)

    effective_threshold = args.threshold
    relaxed = False
    # Auto-relax so the benchmark report is never left without reference creatives: when nothing
    # clears the strict threshold but a near-miss similar candidate exists, drop to --min-threshold.
    if count_accepted(effective_threshold) == 0 and not args.no_auto_relax and args.min_threshold < args.threshold:
        best_similar = max(
            (parse_float(r.get("similarity_score")) or 0 for r in analyzed if r.get("is_similar")),
            default=0.0,
        )
        if best_similar >= args.min_threshold:
            effective_threshold = args.min_threshold
            relaxed = True
            print(
                f"[auto-relax] 0 references passed threshold={args.threshold}; "
                f"relaxing to min_threshold={args.min_threshold} (best similar score={best_similar}).",
                file=sys.stderr,
            )

    json_path, csv_path = write_outputs(analyzed, args.output_dir, effective_threshold, customer, excluded_same_domain_count)
    accepted = count_accepted(effective_threshold)
    print(json.dumps({
        "json": str(json_path),
        "csv": str(csv_path),
        "accepted_count": accepted,
        "excluded_same_domain_count": excluded_same_domain_count,
        "threshold": effective_threshold,
        "requested_threshold": args.threshold,
        "auto_relaxed": relaxed,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
