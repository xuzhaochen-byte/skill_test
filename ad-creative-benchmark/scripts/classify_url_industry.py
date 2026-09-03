#!/usr/bin/env python3
"""Classify a landing-page URL into an Aeolus primary/secondary industry pair.

Input candidates should come from ``aeolus_industry_candidates.py``. The script
uses AIDP ModelHub and writes a small JSON file that can drive CTR Top video
queries when URL->adv_id resolution fails.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from aidp_multimodal_client import chat_completion, extract_text

SYSTEM_PROMPT = """You classify landing pages into valid TikTok Ads Aeolus industry labels. Return JSON only. Choose exactly one candidate primary/secondary pair from the provided list; do not invent labels."""

USER_PROMPT = """Classify the customer's landing page into one valid Aeolus industry candidate.

Customer URL: {url}
Country hint: {country}
Landing page summary or product facts:
{summary}

Valid candidates, choose one by rank/index exactly:
{candidates}

Return JSON only:
{{
  "country": "country code to use for Top video query",
  "primary_industry": "exact Primary Industry from a candidate",
  "secondary_industry": "exact Secondary Industry from the same candidate",
  "candidate_rank": 1,
  "confidence": 0.0,
  "reason": "brief reason grounded in the URL/product evidence"
}}
"""


def extract_json(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError(f"Model did not return JSON: {text[:500]}")
    return json.loads(match.group(0))


def load_summary(path: Path) -> str:
    if not path:
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        data = json.loads(text)
    except Exception:
        return text[:5000]
    interesting: List[str] = []
    for key in ["url", "title", "brand", "product_name", "category", "price", "offer", "summary", "description", "supported_claims", "visual_summary"]:
        value = data.get(key) if isinstance(data, dict) else None
        if value:
            interesting.append(f"{key}: {json.dumps(value, ensure_ascii=False)[:1000]}")
    if not interesting:
        interesting.append(json.dumps(data, ensure_ascii=False)[:5000])
    return "\n".join(interesting)[:5000]


def candidate_lines(path: Path, max_candidates: int) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("candidates") or data.get("rows") or []
    out: List[Dict[str, Any]] = []
    for row in rows[:max_candidates]:
        primary = str(row.get("Primary Industry") or "").strip()
        secondary = str(row.get("Secondary Industry") or "").strip()
        country = str(row.get("Ad Country Code") or "").strip()
        if not primary or not secondary:
            continue
        out.append({
            "rank": row.get("rank") or len(out) + 1,
            "country": country,
            "primary_industry": primary,
            "secondary_industry": secondary,
            "impressions": row.get("Impressions"),
            "clicks": row.get("Clicks (Destination)"),
        })
    if not out:
        raise ValueError(f"No valid industry candidates in {path}")
    return out


def validate_choice(choice: Dict[str, Any], candidates: List[Dict[str, Any]], country_hint: str) -> Dict[str, Any]:
    primary = str(choice.get("primary_industry") or "").strip()
    secondary = str(choice.get("secondary_industry") or "").strip()
    country = str(choice.get("country") or country_hint or "").strip().upper()
    for cand in candidates:
        if cand["primary_industry"] == primary and cand["secondary_industry"] == secondary:
            if not country:
                country = str(cand.get("country") or "").strip().upper()
            return {
                "country": country,
                "primary_industry": primary,
                "secondary_industry": secondary,
                "industry": f"{primary}-{secondary}",
                "candidate_rank": cand.get("rank"),
                "confidence": choice.get("confidence"),
                "reason": choice.get("reason"),
                "candidate": cand,
            }
    raise ValueError(f"Model chose industry not present in candidates: {primary}-{secondary}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify a URL/product summary into a valid Aeolus primary/secondary industry candidate.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--industry-candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--country", default="")
    parser.add_argument("--url-summary", type=Path, help="Optional JSON/text summary from URL crawl/caption brief")
    parser.add_argument("--model", default="gemini-2.5-pro")
    parser.add_argument("--endpoint", default="https://aidp.bytedance.net/api/modelhub/online/multimodal/crawl")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-candidates", type=int, default=120)
    args = parser.parse_args()

    candidates = candidate_lines(args.industry_candidates, args.max_candidates)
    summary = load_summary(args.url_summary) if args.url_summary else "[no crawl summary supplied; infer from URL/domain/path only]"
    prompt = USER_PROMPT.format(url=args.url, country=args.country.strip().upper(), summary=summary, candidates=json.dumps(candidates, ensure_ascii=False, indent=2))
    payload = chat_completion(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        model=args.model,
        max_tokens=args.max_tokens,
        endpoint=args.endpoint,
        timeout=args.timeout,
        temperature=0,
    )
    choice = extract_json(extract_text(payload))
    result = validate_choice(choice, candidates, args.country.strip().upper())
    result["input_url"] = args.url
    result["method"] = "aidp_url_to_aeolus_industry_candidate"
    result["candidates_source"] = str(args.industry_candidates)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
