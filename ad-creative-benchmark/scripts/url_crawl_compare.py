#!/usr/bin/env python3
"""
Lightweight landing-page crawler/enricher for TikTok creative autotest inputs.

Input: CSV with at least raw_url; optional case_id/advertiser_id/raw result.
Output: CSV or JSONL containing final_url (after redirects), fetch status, extracted
fields, and creative-ready image-funnel fields.

Example:
  python3 url_crawl_compare.py \
    --input '/Users/bytedance/Desktop/agentic_ad_creation/tmp/url eg - Sheet1.csv' \
    --output crawl_output.csv \
    --format csv

This intentionally does NOT load/install any Codex skill. It is a standalone script.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import base64
import html
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    print(
        "Missing dependency. Install with: python3 -m pip install requests beautifulsoup4",
        file=sys.stderr,
    )
    raise

try:
    from PIL import Image, ImageDraw, ImageStat
except ImportError:
    Image = None
    ImageDraw = None
    ImageStat = None

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8,fr;q=0.7",
}

DEFAULT_LLM_RETRY_ATTEMPTS = int(os.environ.get("LLM_RETRY_ATTEMPTS", "20"))
DEFAULT_LLM_RETRY_SLEEP_SECONDS = float(os.environ.get("LLM_RETRY_SLEEP_SECONDS", "10"))
DEFAULT_MODELHUB_BASE_ENDPOINT = "https://aidp-i18ntt-sg.tiktok-row.net/api/modelhub/online/v2/crawl"


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

URL_RE = re.compile(r"https?://[^\s\"'<>),]+", re.I)

CTA_KEYWORDS = [
    "add to cart",
    "buy now",
    "order now",
    "shop now",
    "try now",
    "checkout",
    "añadir",
    "agregar",
    "comprar",
    "ajouter",
    "panier",
]

VIDEO_EXT_RE = re.compile(r"\.(mp4|mov|webm|m3u8)(\?|#|$)", re.I)
IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|webp|gif|avif|svg)(\?|#|$)", re.I)
PRICE_RE = re.compile(
    r"(USD\s*)?\$\s?\d+[\d,.]*|€\s?\d+[\d,.]*|EUR\s?\d+[\d,.]*|£\s?\d+[\d,.]*|KSh\s?\d+[\d,.]*|₡\s?\d+[\d,.]*",
    re.I,
)

IMAGE_HARD_REJECT_KEYWORDS = [
    "icon",
    "favicon",
    "sprite",
    "payment",
    "visa",
    "mastercard",
    "paypal",
    "klarna",
    "afterpay",
    "affirm",
    "badge",
    "seal",
    "review-star",
    "footer",
    "facebook",
    "instagram",
    "youtube",
    "pinterest",
    "twitter",
    "x-logo",
]
LOGO_KEYWORDS = ["logo", "brandmark"]
TEXT_HEAVY_KEYWORDS = [
    "nutrition",
    "facts",
    "ingredients",
    "instructions",
    "how-to",
    "how_to",
    "benefits",
    "comparison",
    "compare",
    "review",
    "testimonial",
    "faq",
    "spec",
    "details",
    "chart",
]
PRODUCT_POSITIVE_KEYWORDS = ["pdp", "hero", "product", "front", "main", "pack", "bottle", "jar", "serum", "cream"]
LIFESTYLE_KEYWORDS = ["lifestyle", "model", "use", "using", "routine", "before", "after", "cup", "glass", "smoothie"]
INGREDIENT_KEYWORDS = ["ingredient", "cocoa", "fruit", "mushroom", "protein", "collagen", "formula"]
HOW_TO_KEYWORDS = ["instructions", "how-to", "how_to", "directions", "steps"]
SPEC_KEYWORDS = ["nutrition", "facts", "spec", "details", "label", "supplement"]
BENEFIT_KEYWORDS = ["benefit", "benefits", "feature", "features"]
BUNDLE_KEYWORDS = ["bundle", "kit", "starter", "variety", "pack"]
NAV_OR_COLLECTION_KEYWORDS = ["nav", "menu", "collection", "tile", "recommended", "related", "prebiotic", "keto", "hot"]
OTHER_FLAVOR_TERMS = {
    "vanilla", "latte", "banana", "bread", "berries", "blueberry", "muffin", "brownie",
    "butter", "coffee", "pb", "peanut", "mint", "pineapple", "coconut", "salted",
    "caramel", "plant", "based", "cinnamon", "cinnammon", "cookies", "cream", "strawberry",
    "lemonade", "elderberry", "sleep", "immunity", "multivitamin", "tumbler",
    "prebiotic", "keto", "hot",
}
STOP_TOKENS = {
    "the", "and", "for", "with", "from", "shop", "www", "com", "products", "product", "pdp",
    "new", "page", "image", "images", "front", "main", "hero", "all", "your", "you", "our",
    "a", "an", "to", "of", "in", "on", "by", "or", "is", "are", "as", "at", "it",
}


def norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(norm(v) for v in value if norm(v))
    return re.sub(r"\s+", " ", str(value)).strip()


def uniq(seq: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in seq:
        item = norm(item)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def is_retryable_llm_http_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def post_llm_json_with_sleep_retry(
    endpoint: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: int,
    *,
    attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS,
    sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS,
    validate: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """POST one LLM request with sleep/retry and optional response validation."""
    last_error: Optional[BaseException] = None
    attempts = max(1, int(attempts or 1))
    sleep_seconds = max(0.0, float(sleep_seconds or 0.0))
    for attempt in range(1, attempts + 1):
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
            if attempt >= attempts:
                raise RuntimeError(f"LLM call failed after {attempt} attempt(s): {exc}") from exc
            print(f"[llm:retry] attempt {attempt}/{attempts} failed: {exc}; sleep {sleep_seconds}s", file=sys.stderr, flush=True)
            time.sleep(sleep_seconds)
    raise RuntimeError(f"LLM call failed: {last_error}")


def tokenize(text: str, min_len: int = 3) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in tokens if len(t) >= min_len and t not in STOP_TOKENS]


def absolutize(base_url: str, maybe_url: str) -> str:
    maybe_url = norm(maybe_url)
    if not maybe_url or maybe_url.startswith("data:"):
        return ""
    return urljoin(base_url, maybe_url)


def meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return norm(tag["content"])
    return ""


def parse_srcset(srcset: str) -> List[str]:
    urls = []
    for part in (srcset or "").split(","):
        token = part.strip().split(" ")[0]
        if token:
            urls.append(token)
    return urls


def parse_json_ld(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    objs: List[Dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, list):
            for item in x:
                walk(item)
        elif isinstance(x, dict):
            objs.append(x)
            if "@graph" in x:
                walk(x["@graph"])

    for script in soup.find_all("script", type=lambda v: v and "ld+json" in v):
        text = (script.string or script.get_text() or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        walk(data)
    return objs


def jsonld_type_has(obj: Dict[str, Any], expected: str) -> bool:
    typ = obj.get("@type") or obj.get("type")
    if isinstance(typ, list):
        return any(str(t).lower() == expected.lower() for t in typ)
    return str(typ).lower() == expected.lower()


def first_product_jsonld(jsonlds: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for obj in jsonlds:
        if jsonld_type_has(obj, "Product"):
            return obj
    return None


def extract_brand_from_jsonld(product: Dict[str, Any], jsonlds: List[Dict[str, Any]]) -> str:
    brand = product.get("brand") if product else None
    if isinstance(brand, dict):
        brand = brand.get("name")
    if brand:
        return norm(brand)
    for obj in jsonlds:
        if jsonld_type_has(obj, "Organization") or jsonld_type_has(obj, "Brand"):
            if obj.get("name"):
                return norm(obj.get("name"))
    return ""


def extract_images(soup: BeautifulSoup, base_url: str, product: Optional[Dict[str, Any]]) -> List[str]:
    images: List[str] = []
    if product:
        img = product.get("image")
        raw_images: List[Any]
        if isinstance(img, list):
            raw_images = img
        elif img:
            raw_images = [img]
        else:
            raw_images = []
        for item in raw_images:
            if isinstance(item, dict):
                item = item.get("url") or item.get("contentUrl")
            u = absolutize(base_url, str(item or ""))
            if u:
                images.append(u)
    for name in ["og:image", "twitter:image", "image"]:
        u = absolutize(base_url, meta_content(soup, name))
        if u:
            images.append(u)
    for img in soup.find_all("img"):
        candidates = [img.get("src"), img.get("data-src"), img.get("data-original")]
        candidates += parse_srcset(img.get("srcset") or img.get("data-srcset") or "")
        for cand in candidates:
            u = absolutize(base_url, cand or "")
            if u:
                images.append(u)
    html_text = str(soup)
    for m in re.finditer(r"background-image\s*:\s*url\(['\"]?([^\)'\"]+)", html_text, re.I):
        u = absolutize(base_url, m.group(1))
        if u:
            images.append(u)
    return [u for u in uniq(images) if IMAGE_EXT_RE.search(u) or "cdn" in u.lower()]


def extract_videos(soup: BeautifulSoup, base_url: str, product: Optional[Dict[str, Any]]) -> List[str]:
    videos: List[str] = []
    if product:
        for key in ["video", "contentUrl", "embedUrl"]:
            val = product.get(key)
            vals = val if isinstance(val, list) else [val] if val else []
            for item in vals:
                if isinstance(item, dict):
                    item = item.get("contentUrl") or item.get("embedUrl") or item.get("url")
                u = absolutize(base_url, str(item or ""))
                if u:
                    videos.append(u)
    for tag in soup.find_all(["video", "source", "iframe"]):
        for attr in ["src", "data-src", "data-video-src"]:
            u = absolutize(base_url, tag.get(attr) or "")
            if u:
                videos.append(u)
    html_text = str(soup)
    for m in re.finditer(r"https?://[^\s'\"<>]+(?:mp4|mov|webm|m3u8)(?:\?[^\s'\"<>]*)?", html_text, re.I):
        videos.append(m.group(0))
    return [u for u in uniq(videos) if VIDEO_EXT_RE.search(u) or "youtube" in u.lower() or "vimeo" in u.lower()]


def extract_ctas(soup: BeautifulSoup) -> List[str]:
    ctas: List[str] = []
    for tag in soup.find_all(["button", "a", "input"]):
        text = tag.get("value") if tag.name == "input" else tag.get_text(" ")
        text = norm(text)
        if not text or len(text) > 120:
            continue
        low = text.lower()
        if any(keyword in low for keyword in CTA_KEYWORDS):
            ctas.append(text)
    return uniq(ctas)[:20]


def extract_price(soup: BeautifulSoup, product: Optional[Dict[str, Any]]) -> str:
    if product:
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            price = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
            currency = offers.get("priceCurrency") or ""
            if price:
                return norm(f"{currency} {price}" if currency else price)
    for name in ["product:price:amount", "og:price:amount", "twitter:data1"]:
        val = meta_content(soup, name)
        if val:
            currency = meta_content(soup, "product:price:currency", "og:price:currency")
            return norm(f"{currency} {val}" if currency else val)
    text = norm(soup.get_text(" "))
    m = PRICE_RE.search(text)
    return norm(m.group(0)) if m else ""


def extract_reviews(product: Optional[Dict[str, Any]]) -> List[str]:
    reviews: List[str] = []
    if not product:
        return reviews
    raw = product.get("review") or []
    if isinstance(raw, dict):
        raw = [raw]
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        body = item.get("reviewBody") or item.get("description") or item.get("name")
        author = item.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        rating = item.get("reviewRating")
        if isinstance(rating, dict):
            rating = rating.get("ratingValue")
        text = norm(body)
        if text:
            prefix = f"{rating}★ " if rating else ""
            suffix = f" — {author}" if author else ""
            reviews.append(prefix + text + suffix)
    return reviews



@dataclass
class ImageUrlInfo:
    original_url: str
    normalized_url: str
    normalized_key: str
    width: int = 0
    height: int = 0
    size_score: int = 0
    extension: str = ""


@dataclass
class ImageCandidate:
    url: str
    normalized_key: str
    width: int = 0
    height: int = 0
    size_score: int = 0
    score: float = 0.0
    bucket: str = "unknown"
    text_density: str = "unknown"
    text_role: str = "none"
    decision: str = "drop_useless"
    local_path: str = ""
    download_status: str = ""
    visual_review_bucket: str = ""
    visual_confidence: float = 0.0
    visual_review_reason: str = ""
    visual_keep_for_video: bool = False
    visual_keep_for_copy: bool = False
    visual_extracted_text: str = ""
    visual_key_claims: List[str] = field(default_factory=list)
    visual_brand_text: str = ""
    visual_product_text: str = ""
    reason: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PageContext:
    brand_name: str
    product_name: str
    page_slug: str
    category_terms: List[str]
    description_terms: List[str]

    @property
    def product_tokens(self) -> List[str]:
        return tokenize(self.product_name)

    @property
    def slug_tokens(self) -> List[str]:
        return tokenize(self.page_slug)

    @property
    def brand_tokens(self) -> List[str]:
        return tokenize(self.brand_name)

    @property
    def all_positive_tokens(self) -> List[str]:
        return uniq(self.product_tokens + self.slug_tokens + self.brand_tokens + self.category_terms)


@dataclass
class WebSearchCandidate:
    source_page_url: str
    title: str = ""
    snippet: str = ""
    image_url: str = ""
    source_type: str = "web_search"
    brand_match: str = "none"
    product_match: str = "none"
    relevance_score: float = 0.0
    recommended_use: str = "reject"
    reason: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def strip_shopify_size_suffix(path: str) -> str:
    return re.sub(r"_(?:\d+x\d*|x\d+|small|medium|large|grande|master)(?=\.[A-Za-z0-9]+$)", "", path)


def normalize_image_url(url: str) -> ImageUrlInfo:
    parsed = urlparse(url.strip())
    scheme = "https" if parsed.scheme in ("http", "https", "") else parsed.scheme
    netloc = parsed.netloc.lower()
    path = strip_shopify_size_suffix(parsed.path)
    qs = parse_qs(parsed.query)
    width = 0
    height = 0
    for key in ("width", "w"):
        if qs.get(key):
            try:
                width = max(width, int(qs[key][0]))
            except Exception:
                pass
    for key in ("height", "h"):
        if qs.get(key):
            try:
                height = max(height, int(qs[key][0]))
            except Exception:
                pass
    m = re.search(r"_(\d+)x(\d*)\.", parsed.path)
    if m:
        try:
            width = max(width, int(m.group(1)))
            if m.group(2):
                height = max(height, int(m.group(2)))
        except Exception:
            pass
    keep_params = {}
    for key in ("v",):
        if key in qs:
            keep_params[key] = qs[key][0]
    normalized_url = urlunparse((scheme, netloc, path, "", urlencode(keep_params), ""))
    normalized_key = f"{netloc}{path}".lower()
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    explicit_size = max(width, height)
    # Prefer original no-query URLs when no explicit dimensions are present; otherwise use explicit size.
    size_score = explicit_size if explicit_size else (10**9 if not parsed.query else 0)
    return ImageUrlInfo(
        original_url=url,
        normalized_url=normalized_url,
        normalized_key=normalized_key,
        width=width,
        height=height,
        size_score=size_score,
        extension=ext,
    )


def dedupe_keep_highest_resolution(images: List[str]) -> List[ImageCandidate]:
    best: Dict[str, Tuple[ImageUrlInfo, str]] = {}
    for url in images:
        info = normalize_image_url(url)
        current = best.get(info.normalized_key)
        if current is None or info.size_score > current[0].size_score:
            best[info.normalized_key] = (info, url)
    candidates: List[ImageCandidate] = []
    for info, chosen_url in best.values():
        candidates.append(
            ImageCandidate(
                url=chosen_url,
                normalized_key=info.normalized_key,
                width=info.width,
                height=info.height,
                size_score=info.size_score,
                reason=["dedup_keep_highest_resolution"],
            )
        )
    return candidates


def hard_reject_images(candidates: List[ImageCandidate]) -> Tuple[List[ImageCandidate], List[ImageCandidate]]:
    kept: List[ImageCandidate] = []
    rejected: List[ImageCandidate] = []
    for cand in candidates:
        low = cand.url.lower()
        path = urlparse(cand.url).path.lower()
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        reasons: List[str] = []
        if cand.url.startswith("data:"):
            reasons.append("data_uri")
        if ext == "svg":
            # Logo SVGs are useful as logo-only but not visual generation candidates.
            if any(k in low for k in LOGO_KEYWORDS):
                cand.bucket = "logo"
                cand.decision = "keep_logo_only"
                cand.text_density = "low"
                cand.text_role = "package_label"
                cand.reason.append("logo_svg")
                rejected.append(cand)
                continue
            reasons.append("svg")
        if any(k in low for k in IMAGE_HARD_REJECT_KEYWORDS):
            reasons.append("hard_reject_keyword")
        explicit = max(cand.width, cand.height)
        if explicit and explicit < 300:
            reasons.append("small_dimension")
        if reasons:
            cand.bucket = "ui_or_icon"
            cand.decision = "drop_useless"
            cand.reason.extend(reasons)
            rejected.append(cand)
        else:
            kept.append(cand)
    return kept, rejected


def detect_text_density_and_role(cand: ImageCandidate) -> Tuple[str, str, List[str]]:
    low = cand.url.lower()
    reasons: List[str] = []
    if any(k in low for k in ["nutrition", "facts", "supplement"]):
        return "high", "nutrition_or_specs", ["text_heavy_nutrition_or_specs"]
    if any(k in low for k in HOW_TO_KEYWORDS):
        return "high", "instructions", ["text_heavy_instructions"]
    if any(k in low for k in ["benefits", "benefit", "comparison", "compare", "details", "chart", "faq"]):
        return "high", "benefit_explainer", ["text_heavy_benefit_or_chart"]
    if any(k in low for k in ["review", "testimonial"]):
        return "high", "review_or_testimonial", ["text_heavy_review_or_testimonial"]
    if any(k in low for k in ["promo", "offer", "sale"]):
        return "medium", "promo_offer", ["medium_text_promo"]
    if "logo" in low:
        return "low", "package_label", ["low_text_logo"]
    return "low", "package_label", reasons


def classify_image_by_rules(cand: ImageCandidate, context: PageContext) -> ImageCandidate:
    low = cand.url.lower()
    density, role, text_reasons = detect_text_density_and_role(cand)
    cand.text_density = density
    cand.text_role = role
    cand.reason.extend(text_reasons)
    if any(k in low for k in LOGO_KEYWORDS):
        cand.bucket = "logo"
        cand.decision = "keep_logo_only"
        cand.reason.append("logo_keyword")
        return cand
    if any(k in low for k in SPEC_KEYWORDS):
        cand.bucket = "spec_or_nutrition"
    elif any(k in low for k in HOW_TO_KEYWORDS):
        cand.bucket = "how_to_use"
    elif any(k in low for k in BENEFIT_KEYWORDS):
        cand.bucket = "benefit"
    elif any(k in low for k in INGREDIENT_KEYWORDS):
        cand.bucket = "ingredient"
    elif any(k in low for k in LIFESTYLE_KEYWORDS):
        cand.bucket = "lifestyle"
    elif any(k in low for k in BUNDLE_KEYWORDS):
        cand.bucket = "related_or_bundle"
    elif any(k in low for k in PRODUCT_POSITIVE_KEYWORDS):
        cand.bucket = "product"
    else:
        cand.bucket = "unknown"

    # Treat obvious other flavors/SKUs as other product when they do not belong to the page tokens.
    slug_tokens = set(context.slug_tokens + context.product_tokens)
    path_tokens = set(tokenize(urlparse(cand.url).path))
    flavor_hits = sorted((path_tokens & OTHER_FLAVOR_TERMS) - slug_tokens)
    if flavor_hits and not set(context.slug_tokens).issuperset(flavor_hits):
        # Do not punish generic ingredient/protein words; only clear alternate SKU terms.
        if not ("chocolate" in slug_tokens and flavor_hits == ["protein"]):
            cand.bucket = "other_product"
            cand.reason.append("other_sku_or_flavor:" + ",".join(flavor_hits[:4]))

    if any(k in low for k in NAV_OR_COLLECTION_KEYWORDS):
        cand.reason.append("nav_or_collection_image")

    return cand


def score_image_relevance(cand: ImageCandidate, context: PageContext) -> ImageCandidate:
    low = cand.url.lower()
    path_text = " ".join(tokenize(urlparse(cand.url).path, min_len=2))
    score = 0.0
    product_matches = [t for t in context.product_tokens if t in path_text]
    slug_matches = [t for t in context.slug_tokens if t in path_text]
    brand_matches = [t for t in context.brand_tokens if t in path_text]
    if product_matches:
        score += min(0.35, 0.12 * len(product_matches))
        cand.reason.append("matches_product_token:" + ",".join(product_matches[:4]))
    if slug_matches:
        score += min(0.35, 0.10 * len(slug_matches))
        cand.reason.append("matches_page_slug:" + ",".join(slug_matches[:4]))
    if brand_matches:
        score += min(0.12, 0.04 * len(brand_matches))
        cand.reason.append("matches_brand_token:" + ",".join(brand_matches[:3]))
    visual_keyword_hits = [k for k in ["pdp", "hero", "product", "front", "main", "firstpic"] if k in low]
    if visual_keyword_hits:
        # Generic words like product/front/main are weak; PDP/hero/current product-token matches are stronger.
        if {"pdp", "hero", "firstpic"} & set(visual_keyword_hits) or product_matches or slug_matches:
            score += 0.20
        else:
            score += 0.06
        cand.reason.append("product_visual_keyword")
    if context.brand_tokens and any(t in low for t in context.brand_tokens):
        score += 0.08
        cand.reason.append("brand_token_in_url")
    explicit = max(cand.width, cand.height)
    if cand.size_score >= 1000 or explicit >= 1000:
        score += 0.15
        cand.reason.append("high_resolution")
    elif explicit and explicit < 500:
        score -= 0.15
        cand.reason.append("low_resolution")
    if cand.bucket in {"product", "ingredient", "lifestyle"}:
        score += 0.12
    if cand.bucket in {"benefit", "how_to_use", "spec_or_nutrition"}:
        score += 0.08
    if cand.bucket == "related_or_bundle":
        score -= 0.05
    if cand.bucket == "other_product":
        score -= 0.55
    if cand.text_density == "high":
        score -= 0.10
        cand.reason.append("text_heavy_not_main_visual")
    if any(k in low for k in NAV_OR_COLLECTION_KEYWORDS):
        score -= 0.35
    cand.score = round(max(0.0, min(1.0, score)), 3)
    return cand



def _matched_current_product_token_count(cand: ImageCandidate) -> int:
    matched = set()
    for reason in cand.reason:
        if reason.startswith("matches_product_token:") or reason.startswith("matches_page_slug:"):
            _, values = reason.split(":", 1)
            matched.update(t for t in values.split(",") if t)
    return len(matched)


def _looks_like_current_product(cand: ImageCandidate, context: PageContext) -> bool:
    # In strict mode, avoid letting same-brand accessories/nav products into final creative images.
    # Require at least two current product/page tokens when possible. For pages whose filenames
    # are opaque but where only one distinctive slug/product token appears (common on Shopify),
    # allow a single match if the image has high resolution and no explicit other-SKU signal.
    match_count = _matched_current_product_token_count(cand)
    if match_count >= 2 and "nav_or_collection_image" not in " ".join(cand.reason):
        return True
    distinctive = [t for t in uniq(context.product_tokens + context.slug_tokens) if t not in {"anti", "cellulite", "women", "results", "visible", "days", "serum", "boosted", "skin", "tint", "balm", "dark", "spot", "eye", "cream"}]
    if match_count >= 1 and cand.size_score >= 800 and "other_sku_or_flavor" not in " ".join(cand.reason):
        return True
    # Some Shopify pages use opaque generated filenames for the true PDP image; allow
    # Firstpic-style filenames, but do not broadly trust generic main/hero/product
    # filenames because they often belong to nav tiles or unrelated add-ons.
    low = cand.url.lower()
    if "firstpic" in low and cand.size_score >= 800 and "other_sku_or_flavor" not in " ".join(cand.reason):
        return True
    return False


def _selection_priority(cand: ImageCandidate) -> int:
    low = cand.url.lower()
    if "hero" in low and cand.text_density != "high":
        return 50
    if "firstpic" in low and cand.text_density != "high":
        return 48
    if cand.bucket == "product":
        return 45 if _matched_current_product_token_count(cand) >= 1 else 12
    if cand.bucket == "lifestyle":
        return 40
    if cand.bucket == "ingredient":
        return 35
    if cand.bucket == "benefit":
        return 25
    if cand.bucket == "how_to_use":
        return 20
    if cand.bucket == "related_or_bundle":
        return 10
    return 0

def select_visual_candidates(candidates: List[ImageCandidate], limit: int) -> List[ImageCandidate]:
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (
            c.score,
            _selection_priority(c),
            0 if c.bucket == "other_product" else 1,
            c.size_score,
        ),
        reverse=True,
    )
    return sorted_candidates[:limit]


def _is_opaque_shopify_highres_candidate(cand: ImageCandidate) -> bool:
    """Return true for high-res Shopify CDN assets whose filenames carry little SEO signal.

    Many Shopify PDPs use generated UUID-ish files under /cdn/shop/files/.  Rule scoring
    cannot confidently match these to the product/slug, but they are often the exact PDP
    gallery images a visual model should inspect.  This function only expands the visual
    review pool; final keep/drop is still decided by the visual review / later selectors.
    """
    low = cand.url.lower()
    if "/cdn/shop/files/" not in low:
        return False
    if cand.size_score < 1000:
        return False
    if cand.bucket in {"logo", "ui_or_icon", "other_product"}:
        return False
    if cand.text_density == "high":
        return False
    if any(reason in cand.reason for reason in ["hard_reject", "tiny_image", "icon_or_ui_asset"]):
        return False
    filename = Path(urlparse(low).path).name.rsplit(".", 1)[0]
    # Treat hash/uuid/numeric-ish names and short locale prefixes as opaque.  Human
    # readable names are already handled well by normal relevance scoring.
    alpha_chunks = re.findall(r"[a-z]+", filename)
    long_words = [chunk for chunk in alpha_chunks if len(chunk) >= 6]
    has_uuidish = bool(re.search(r"[0-9a-f]{6,}[-_][0-9a-f]{4,}", filename))
    mostly_numeric_or_short = not long_words or filename.startswith(("fr_", "en_", "60_", "61_"))
    return has_uuidish or mostly_numeric_or_short


def select_visual_candidates_with_recall(candidates: List[ImageCandidate], limit: int, extra_shopify_recall: int = 0) -> List[ImageCandidate]:
    selected = select_visual_candidates(candidates, limit)
    if extra_shopify_recall <= 0:
        return selected
    selected_keys = {c.normalized_key for c in selected}
    tail = [c for c in candidates if c.normalized_key not in selected_keys and _is_opaque_shopify_highres_candidate(c)]
    tail = sorted(tail, key=lambda c: (c.size_score, c.score), reverse=True)
    selected.extend(tail[:extra_shopify_recall])
    return selected


def select_creative_ready_images(
    candidates: List[ImageCandidate],
    max_images: int,
    text_heavy_policy: str = "separate",
    context: Optional[PageContext] = None,
) -> Dict[str, List[ImageCandidate]]:
    creative: List[ImageCandidate] = []
    text_heavy: List[ImageCandidate] = []
    copy_source: List[ImageCandidate] = []
    rejected: List[ImageCandidate] = []
    logos: List[ImageCandidate] = []

    for cand in sorted(candidates, key=lambda c: (_selection_priority(c), c.score, c.size_score), reverse=True):
        if cand.bucket == "logo":
            cand.decision = "keep_logo_only"
            logos.append(cand)
            continue
        if cand.bucket == "other_product":
            cand.decision = "drop_other_product"
            rejected.append(cand)
            continue
        if cand.score < 0.15 and cand.bucket == "unknown":
            cand.decision = "drop_useless"
            cand.reason.append("low_relevance_score")
            rejected.append(cand)
            continue
        current_match = _looks_like_current_product(cand, context)
        if cand.text_density == "high":
            if not current_match:
                cand.decision = "drop_other_product" if cand.bucket == "other_product" else "drop_useless"
                cand.reason.append("text_heavy_not_current_product")
                rejected.append(cand)
                continue
            if text_heavy_policy == "include" and len(creative) < max_images:
                cand.decision = "keep_visual"
                creative.append(cand)
            elif text_heavy_policy == "exclude":
                cand.decision = "drop_useless"
                rejected.append(cand)
            else:
                cand.decision = "keep_reference"
                text_heavy.append(cand)
                copy_source.append(cand)
            continue
        if cand.bucket in {"product", "lifestyle", "ingredient", "benefit", "how_to_use", "unknown", "related_or_bundle"} and cand.score >= 0.15 and current_match:
            if len(creative) < max_images:
                cand.decision = "keep_visual"
                creative.append(cand)
            else:
                cand.decision = "keep_reference"
                rejected.append(cand)
        else:
            cand.decision = "drop_other_product" if cand.score >= 0.25 and not current_match else "drop_useless"
            if cand.score >= 0.25 and not current_match:
                cand.reason.append("insufficient_current_product_token_match")
            rejected.append(cand)

    return {
        "creative_ready_visual": creative[:max_images],
        "text_heavy": text_heavy,
        "copy_source": copy_source,
        "logos": logos[:1],
        "rejected": rejected + logos[1:],
    }


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    last = path.split("/")[-1] if path else ""
    return re.sub(r"[^a-zA-Z0-9]+", "-", last).strip("-").lower()


def build_page_context(result: "CrawlResult", final_url: str, categories: List[str]) -> PageContext:
    description_terms = tokenize(result.description)[:20]
    return PageContext(
        brand_name=result.brand_name,
        product_name=result.product_name,
        page_slug=slug_from_url(final_url),
        category_terms=uniq([t for c in categories for t in tokenize(c)]),
        description_terms=description_terms,
    )


def _search_text_tokens(value: str) -> List[str]:
    return [t for t in tokenize(value) if t not in STOP_TOKENS and len(t) >= 3]


def classify_external_source(url: str, title: str, snippet: str, context: PageContext, final_url: str = "") -> str:
    netloc = urlparse(url).netloc.lower().replace("www.", "")
    final_host = urlparse(final_url).netloc.lower().replace("www.", "") if final_url else ""
    brand = norm(context.brand_name).lower()
    if final_host and netloc.endswith(final_host):
        return "official_site"
    if brand and brand.replace(" ", "") in netloc.replace("-", "").replace(".", ""):
        return "likely_official_site"
    if any(s in netloc for s in ["tiktok.com", "instagram.com", "youtube.com", "youtu.be", "facebook.com", "pinterest.com"]):
        return "social_search"
    if any(s in netloc for s in ["amazon.", "walmart.", "target.", "costco.", "shopify."]):
        return "marketplace_or_retail"
    return "web_search"


def score_web_search_candidate(candidate: WebSearchCandidate, context: PageContext) -> WebSearchCandidate:
    hay = " ".join([candidate.title, candidate.snippet, candidate.source_page_url, candidate.image_url]).lower()
    brand_tokens = _search_text_tokens(context.brand_name)
    product_tokens = _search_text_tokens(context.product_name)
    # SKU/product terms are stricter than generic category words, but keep tokens for short names.
    generic_terms = {"protein", "smoothie", "serum", "cream", "bottle", "pitcher", "spray"}
    strict_product_tokens = [t for t in product_tokens if t not in generic_terms]
    if strict_product_tokens:
        product_tokens = strict_product_tokens
    brand_hits = [t for t in brand_tokens if t in hay]
    product_hits = [t for t in product_tokens if t in hay]
    if brand_tokens and len(brand_hits) == len(brand_tokens):
        candidate.brand_match = "exact"
        candidate.relevance_score += 0.35
        candidate.reason.append("brand_exact_match")
    elif brand_hits:
        candidate.brand_match = "partial"
        candidate.relevance_score += 0.15
        candidate.reason.append("brand_partial_match")
    if product_tokens and len(product_hits) >= max(1, int(len(product_tokens) * 0.75)):
        candidate.product_match = "exact"
        candidate.relevance_score += 0.45
        candidate.reason.append("product_strong_match")
    elif product_hits:
        candidate.product_match = "partial"
        candidate.relevance_score += 0.18
        candidate.reason.append("product_partial_match")
    image_tokens = _search_text_tokens(candidate.image_url)
    if candidate.image_url:
        candidate.relevance_score += 0.10
        candidate.reason.append("has_image")
        image_product_hits = [t for t in product_tokens if t in candidate.image_url.lower()]
        if product_tokens and len(image_product_hits) >= max(1, int(len(product_tokens) * 0.5)):
            candidate.reason.append("image_url_product_match")
        elif product_tokens:
            candidate.relevance_score = max(0.0, candidate.relevance_score - 0.25)
            candidate.reason.append("image_url_product_match_weak")
    if candidate.source_type in {"official_site", "likely_official_site"}:
        candidate.relevance_score += 0.10
        candidate.reason.append(candidate.source_type)
    elif candidate.source_type == "social_search":
        candidate.relevance_score += 0.04
        candidate.reason.append("social_source")
    if candidate.image_url and "image_url_product_match_weak" in candidate.reason:
        candidate.recommended_use = "reject"
    elif candidate.brand_match == "exact" and candidate.product_match == "exact" and candidate.image_url:
        candidate.recommended_use = "visual_candidate"
    elif candidate.brand_match in {"exact", "partial"} and candidate.product_match in {"exact", "partial"}:
        candidate.recommended_use = "copy_reference"
    else:
        candidate.recommended_use = "reject"
        candidate.reason.append("strict_brand_product_match_failed")
    candidate.relevance_score = round(min(1.0, candidate.relevance_score), 3)
    return candidate


def _walk_json_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield item
            yield from _walk_json_values(item)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from _walk_json_values(item)


def _coze_extract_result_urls(text: str) -> List[str]:
    """Extract http URLs from Coze stream_run JSON/SSE/plain-text output."""
    urls: List[str] = []

    def add_from_string(s: str) -> None:
        for url in URL_RE.findall(s or ""):
            url = url.rstrip(".],\\")
            if url.startswith("http") and url not in urls:
                urls.append(url)

    add_from_string(text)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        for value in _walk_json_values(obj):
            if isinstance(value, str):
                add_from_string(value)
    return urls


def search_web_urls_coze(
    query: str,
    limit: int = 6,
    endpoint: str = "https://api.coze.com/v1/workflow/stream_run",
    workflow_id: str = "7647383585968422965",
    token: str = "",
    timeout: int = 30,
) -> Tuple[List[str], str]:
    """Search through the user's Coze Google workflow. Token should be passed by CLI or env."""
    if not token:
        return [], "coze_unavailable:missing_token"
    payload = {
        "workflow_id": workflow_id,
        "parameters": {"USER_INPUT": query, "num": max(1, limit)},
    }
    cmd = [
        "curl",
        "-sS",
        "--max-time",
        str(timeout),
        "-X",
        "POST",
        endpoint,
        "-H",
        f"Authorization: Bearer {token}",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps(payload, ensure_ascii=False),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except Exception as exc:
        return [], f"coze_unavailable:{type(exc).__name__}:{str(exc)[:80]}"
    body = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return [], f"coze_unavailable:returncode_{proc.returncode}:{body[:100]}"
    urls = _coze_extract_result_urls(body)
    # Coze API errors are JSON and often contain no URLs; surface the code/message for debugging.
    if not urls:
        try:
            obj = json.loads(body)
            if obj.get("code"):
                return [], f"coze_unavailable:code_{obj.get('code')}:{str(obj.get('msg', ''))[:80]}"
        except Exception:
            pass
    return uniq(urls)[:limit], "coze"


def fetch_source_page_assets(url: str, timeout: int = 12) -> Tuple[str, str, List[str]]:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400 or not resp.text:
            return "", "", []
        soup = BeautifulSoup(resp.text, "html.parser")
        title = meta_content(soup, "og:title", "twitter:title") or norm(soup.title.string if soup.title else "")
        desc = meta_content(soup, "og:description", "description", "twitter:description")
        imgs = []
        for value in [meta_content(soup, "og:image", "twitter:image")]:
            if value:
                imgs.append(urljoin(resp.url, value))
        imgs.extend(extract_images(soup, resp.url, None)[:12])
        return title, desc, uniq(imgs)
    except Exception:
        return "", "", []


def fetch_source_page_assets_rendered(url: str, timeout: int = 20) -> Tuple[str, str, List[str], str]:
    """Fetch rendered DOM/assets with Playwright via Node. Slower but catches JS/lazy content."""
    node_bin = shutil.which("node") or "/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    if not Path(node_bin).exists() and shutil.which("node") is None:
        return "", "", [], "rendered_unavailable:node_not_found"
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    js = r'''
const { chromium } = require('playwright');
const url = process.argv[1];
const timeoutMs = Number(process.argv[2] || 20000);
const executablePath = process.argv[3] || '';
(async () => {
  let browser;
  try {
    const launchOptions = { headless: true };
    if (executablePath) launchOptions.executablePath = executablePath;
    browser = await chromium.launch(launchOptions);
    const page = await browser.newPage({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36',
      viewport: { width: 1365, height: 1600 }
    });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    await page.waitForTimeout(1200);
    await page.evaluate(async () => {
      const sleep = (ms) => new Promise(r => setTimeout(r, ms));
      const h = Math.max(document.body.scrollHeight || 0, document.documentElement.scrollHeight || 0);
      for (let y = 0; y < Math.min(h, 6000); y += 900) {
        window.scrollTo(0, y);
        await sleep(180);
      }
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(700);
    const data = await page.evaluate(() => {
      const abs = (v) => { try { return new URL(v, location.href).href; } catch { return ''; } };
      const meta = (...names) => {
        for (const n of names) {
          const el = document.querySelector(`meta[property="${n}"], meta[name="${n}"]`);
          if (el && el.content) return el.content.trim();
        }
        return '';
      };
      const urls = [];
      const add = (u) => { u = abs(u || ''); if (u && !urls.includes(u)) urls.push(u); };
      add(meta('og:image', 'twitter:image'));
      for (const img of Array.from(document.images || [])) {
        add(img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original'));
        const srcset = img.getAttribute('srcset') || img.getAttribute('data-srcset') || '';
        for (const part of srcset.split(',')) add(part.trim().split(/\s+/)[0]);
      }
      for (const e of performance.getEntriesByType('resource') || []) {
        if (e.initiatorType === 'img' || /\.(png|jpe?g|webp|gif|avif)(\?|#|$)/i.test(e.name)) add(e.name);
      }
      return {
        title: meta('og:title', 'twitter:title') || document.title || '',
        desc: meta('og:description', 'description', 'twitter:description') || (document.body ? document.body.innerText.slice(0, 1200) : ''),
        imgs: urls.slice(0, 80)
      };
    });
    console.log(JSON.stringify(data));
  } catch (err) {
    console.error(String(err && err.message || err));
    process.exitCode = 2;
  } finally {
    if (browser) await browser.close();
  }
})();
'''
    try:
        proc = subprocess.run(
            [node_bin, "-e", js, url, str(timeout * 1000), chrome_path if Path(chrome_path).exists() else ""],
            capture_output=True,
            text=True,
            timeout=timeout + 8,
            env={**os.environ, "NODE_PATH": os.environ.get("NODE_PATH", "/Users/bytedance/node_modules")},
        )
    except Exception as exc:
        return "", "", [], f"rendered_unavailable:{type(exc).__name__}:{str(exc)[:80]}"
    if proc.returncode != 0 or not proc.stdout.strip():
        return "", "", [], f"rendered_failed:returncode_{proc.returncode}:{(proc.stderr or '')[:120]}"
    try:
        data = json.loads(proc.stdout.splitlines()[-1])
    except Exception as exc:
        return "", "", [], f"rendered_parse_failed:{type(exc).__name__}"
    return norm(data.get("title")), norm(data.get("desc")), uniq([u for u in data.get("imgs", []) if isinstance(u, str)]), "rendered_ok"


def should_render_web_source(source_type: str, title: str, snippet: str, imgs: List[str], mode: str) -> bool:
    if mode == "rendered":
        return True
    if mode != "auto":
        return False
    if source_type == "social_search":
        return True
    if len(imgs) < 2:
        return True
    if len(norm(" ".join([title, snippet]))) < 80:
        return True
    return False


def _is_hard_reject_image_url(url: str) -> bool:
    low = url.lower()
    if low.endswith(".svg") or ".svg?" in low:
        return True
    return any(k in low for k in IMAGE_HARD_REJECT_KEYWORDS + LOGO_KEYWORDS)


def _is_strict_source_page(candidate: WebSearchCandidate) -> bool:
    return (
        candidate.recommended_use != "reject"
        and candidate.brand_match in {"exact", "partial"}
        and candidate.product_match == "exact"
        and candidate.source_type in {"official_site", "likely_official_site", "social_search", "marketplace_or_retail"}
    )


def search_web_urls(query: str, limit: int = 6, timeout: int = 12) -> Tuple[List[str], str]:
    """Return organic-ish result URLs using an optional googlesearch package or DDG HTML backup provider."""
    try:
        from googlesearch import search  # type: ignore
        return list(search(query, num_results=max(1, limit), lang="en"))[:limit], "googlesearch"
    except Exception:
        pass
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            headers=DEFAULT_HEADERS,
            data={"q": query},
            timeout=timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        urls: List[str] = []
        for a in soup.select("a.result__a"):
            href = a.get("href") or ""
            if not href:
                continue
            parsed = urlparse(href)
            if parsed.netloc.endswith("duckduckgo.com"):
                qs = parse_qs(parsed.query)
                href = qs.get("uddg", [href])[0]
            if href.startswith("http") and href not in urls:
                urls.append(href)
            if len(urls) >= limit:
                break
        return urls, "duckduckgo_html"
    except Exception as exc:
        return [], f"search_unavailable:{type(exc).__name__}:{str(exc)[:80]}"


def run_strict_web_search(
    context: PageContext,
    final_url: str,
    limit: int = 6,
    image_limit: int = 20,
    fetch_pages: bool = True,
    provider: str = "duckduckgo",
    coze_endpoint: str = "https://api.coze.com/v1/workflow/stream_run",
    coze_workflow_id: str = "7647383585968422965",
    coze_token: str = "",
    fetch_mode: str = "static",
    render_limit: int = 3,
    render_timeout: int = 20,
) -> Dict[str, Any]:
    if not context.brand_name or not context.product_name:
        return {"queries": [], "sources": [], "image_candidates": [], "copy_sources": [], "warnings": ["missing brand/product for strict web search"]}
    queries = uniq([
        f'"{context.brand_name}" "{context.product_name}"',
        f'"{context.brand_name}" "{context.product_name}" review',
        f'"{context.brand_name}" "{context.product_name}" ingredients',
        f'"{context.brand_name}" "{context.product_name}" TikTok',
        f'"{context.brand_name}" "{context.product_name}" Instagram',
    ])
    sources: List[WebSearchCandidate] = []
    image_candidates: List[WebSearchCandidate] = []
    warnings: List[str] = []
    seen_pages = set()
    seen_images = set()
    rendered_count = 0
    for query in queries:
        if provider == "coze":
            results, backend = search_web_urls_coze(
                query,
                limit=limit,
                endpoint=coze_endpoint,
                workflow_id=coze_workflow_id,
                token=coze_token,
            )
        else:
            results, backend = search_web_urls(query, limit=limit)
        if backend.startswith("search_unavailable"):
            warnings.append(f"search_failed:{query}:{backend}")
            continue
        if backend.startswith("coze_unavailable"):
            warnings.append(f"search_failed:{query}:{backend}")
            continue
        for page_url in results:
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            title = ""
            snippet = ""
            imgs: List[str] = []
            if fetch_pages:
                title, snippet, imgs = fetch_source_page_assets(page_url)
            source_type = classify_external_source(page_url, title, snippet, context, final_url)
            if fetch_pages and rendered_count < render_limit and should_render_web_source(source_type, title, snippet, imgs, fetch_mode):
                r_title, r_snippet, r_imgs, r_status = fetch_source_page_assets_rendered(page_url, timeout=render_timeout)
                warnings.append(f"render_fetch:{page_url}:{r_status}")
                rendered_count += 1
                if r_title:
                    title = r_title
                if r_snippet:
                    snippet = r_snippet
                if r_imgs:
                    imgs = uniq(imgs + r_imgs)
                source_type = classify_external_source(page_url, title, snippet, context, final_url)
            base = WebSearchCandidate(
                source_page_url=page_url,
                title=title,
                snippet=snippet,
                source_type=source_type,
            )
            score_web_search_candidate(base, context)
            sources.append(base)
            if not _is_strict_source_page(base):
                continue
            for img in imgs:
                if len(image_candidates) >= image_limit:
                    break
                if _is_hard_reject_image_url(img):
                    continue
                image_key = normalize_image_url(img).normalized_key
                if image_key in seen_images:
                    continue
                seen_images.add(image_key)
                c = WebSearchCandidate(
                    source_page_url=page_url,
                    title=title,
                    snippet=snippet,
                    image_url=img,
                    source_type=base.source_type,
                )
                score_web_search_candidate(c, context)
                if c.recommended_use != "reject":
                    image_candidates.append(c)
    sources = sorted(sources, key=lambda c: c.relevance_score, reverse=True)[: max(limit * len(queries), limit)]
    image_candidates = sorted(image_candidates, key=lambda c: c.relevance_score, reverse=True)[:image_limit]
    copy_sources = [c for c in sources if c.recommended_use in {"copy_reference", "visual_candidate"}]
    return {
        "queries": queries,
        "sources": [c.to_dict() for c in sources],
        "image_candidates": [c.to_dict() for c in image_candidates],
        "copy_sources": [c.to_dict() for c in copy_sources],
        "warnings": warnings,
    }



def safe_filename_from_url(url: str, index: int) -> str:
    parsed = urlparse(url)
    ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else "jpg"
    if ext not in {"jpg", "jpeg", "png", "webp", "gif", "avif"}:
        ext = "jpg"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", parsed.path.rsplit("/", 1)[-1].rsplit(".", 1)[0])[:80] or "image"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{index:02d}_{stem}_{digest}.{ext}"


def download_visual_candidates(candidates: List[ImageCandidate], case_id: str, download_dir: Path, timeout: int = 20) -> None:
    case_dir = download_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    for idx, cand in enumerate(candidates, 1):
        path = case_dir / safe_filename_from_url(cand.url, idx)
        cand.local_path = str(path)
        if path.exists() and path.stat().st_size > 0:
            cand.download_status = "cached"
            continue
        try:
            resp = requests.get(cand.url, headers=DEFAULT_HEADERS, timeout=timeout, stream=True)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type.lower() and not IMAGE_EXT_RE.search(cand.url):
                cand.download_status = f"skipped_non_image:{content_type}"
                continue
            data = resp.content
            if len(data) > 12 * 1024 * 1024:
                cand.download_status = "skipped_too_large"
                continue
            path.write_bytes(data)
            cand.download_status = "downloaded"
        except Exception as exc:
            cand.download_status = f"error:{type(exc).__name__}:{str(exc)[:120]}"


def image_basic_stats(path: str) -> Dict[str, Any]:
    if Image is None:
        return {"error": "pillow_unavailable"}
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            width, height = im.size
            small = im.resize((max(1, min(160, width)), max(1, int(height * min(160, width) / max(1, width)))))
            stat = ImageStat.Stat(small)
            mean = sum(stat.mean) / 3.0
            extrema = stat.extrema
            contrast = sum((hi - lo) for lo, hi in extrema) / 3.0
            # Simple edge density: average adjacent-pixel luminance deltas on a thumbnail.
            gray = small.convert("L")
            pix = gray.load()
            w, h = gray.size
            edges = 0
            samples = 0
            for y in range(0, max(0, h - 1), 2):
                for x in range(0, max(0, w - 1), 2):
                    if abs(int(pix[x, y]) - int(pix[x + 1, y])) > 35 or abs(int(pix[x, y]) - int(pix[x, y + 1])) > 35:
                        edges += 1
                    samples += 1
            edge_density = edges / samples if samples else 0.0
            return {"width": width, "height": height, "mean_brightness": round(mean, 2), "contrast": round(contrast, 2), "edge_density": round(edge_density, 4)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}


def heuristic_visual_review_candidate(cand: ImageCandidate, context: PageContext) -> None:
    stats = image_basic_stats(cand.local_path) if cand.local_path else {"error": "not_downloaded"}
    current = _looks_like_current_product(cand, context)
    text_density = cand.text_density
    text_role = cand.text_role
    if "edge_density" in stats and stats["edge_density"] > 0.34 and cand.bucket in {"benefit", "how_to_use", "spec_or_nutrition", "unknown"}:
        text_density = "high"
        if text_role == "none":
            text_role = "unknown_text"
    joined_reason = " ".join(cand.reason)
    nav_or_collection = "nav_or_collection_image" in joined_reason
    if cand.bucket == "logo":
        review_bucket = "logo"
    elif nav_or_collection:
        review_bucket = "ui_or_icon"
    elif text_density == "high":
        review_bucket = "text_heavy"
    elif cand.bucket == "other_product" or not current:
        review_bucket = "other_product" if cand.score >= 0.20 else "bad_asset"
    elif cand.bucket in {"product", "lifestyle", "ingredient", "benefit", "how_to_use", "spec_or_nutrition"}:
        review_bucket = cand.bucket
    else:
        review_bucket = "product" if current else "unknown"
    cand.visual_review_bucket = review_bucket
    cand.text_density = text_density
    cand.text_role = text_role
    cand.visual_confidence = round(min(0.95, max(0.35, cand.score + (0.15 if cand.download_status in {"downloaded", "cached"} else 0.0))), 2)
    cand.visual_keep_for_video = review_bucket in {"product", "lifestyle", "ingredient", "benefit", "how_to_use"} and text_density != "high" and current and not nav_or_collection
    cand.visual_keep_for_copy = text_density == "high" and current
    cand.visual_review_reason = f"heuristic_visual_review; stats={stats}"
    cand.reason.append("visual_review_heuristic")


def apply_heuristic_visual_review(candidates: List[ImageCandidate], context: PageContext) -> None:
    for cand in candidates:
        heuristic_visual_review_candidate(cand, context)


def create_contact_sheet(candidates: List[ImageCandidate], case_id: str, output_dir: Path, columns: int = 5, thumb_size: int = 220) -> str:
    if Image is None or ImageDraw is None:
        return ""
    imgs = []
    for idx, cand in enumerate(candidates, 1):
        if not cand.local_path or not Path(cand.local_path).exists():
            continue
        try:
            with Image.open(cand.local_path) as im:
                im = im.convert("RGB")
                im.thumbnail((thumb_size, thumb_size))
                tile = Image.new("RGB", (thumb_size, thumb_size + 54), "white")
                tile.paste(im, ((thumb_size - im.width) // 2, 0))
                draw = ImageDraw.Draw(tile)
                label = f"#{idx} {cand.bucket} s={cand.score} txt={cand.text_density}"
                draw.text((6, thumb_size + 6), label[:38], fill="black")
                imgs.append(tile)
        except Exception:
            continue
    if not imgs:
        return ""
    rows = (len(imgs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_size, rows * (thumb_size + 54)), "white")
    for i, tile in enumerate(imgs):
        x = (i % columns) * thumb_size
        y = (i // columns) * (thumb_size + 54)
        sheet.paste(tile, (x, y))
    out = output_dir / f"{case_id}_visual_candidates_contact_sheet.jpg"
    sheet.save(out, quality=88)
    return str(out)


def openai_visual_review_contact_sheet(
    contact_sheet_path: str,
    candidates: List[ImageCandidate],
    context: PageContext,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int = 60,
    llm_retry_attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS,
    llm_retry_sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS,
) -> List[Dict[str, Any]]:
    if not api_key or not contact_sheet_path or not Path(contact_sheet_path).exists():
        raise RuntimeError("openai visual review requires API key and an existing contact sheet")
    data = Path(contact_sheet_path).read_bytes()
    image_b64 = base64.b64encode(data).decode("ascii")
    prompt = {
        "task": "Review numbered product image candidates for creative video generation.",
        "brand_name": context.brand_name,
        "product_name": context.product_name,
        "page_slug": context.page_slug,
        "instructions": [
            "For each numbered image, decide if it is the current product, another product, text-heavy reference, logo, UI/icon, or bad asset.",
            "Separate text-heavy images because video generation models often reproduce text poorly.",
            "Read visible image text when present. Use it as OCR-like evidence for claims, brand, product, benefits, ingredients, specs, and usage copy.",
            "Return only JSON with key reviews. Each review must include index, visual_bucket, is_current_product, text_density, keep_for_video_generation, keep_for_copy_extraction, visible_text, key_claims, brand_text, product_text, confidence, reason."
        ],
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": json.dumps(prompt, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]}
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    def validate(raw_response: Dict[str, Any]) -> None:
        content = _extract_chat_completion_content(raw_response)
        if not content:
            raise RuntimeError("openai visual review returned empty content")
        if not _parse_visual_review_json(content):
            raise RuntimeError("openai visual review returned no parseable reviews")

    raw = post_llm_json_with_sleep_retry(
        base_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload=payload,
        timeout=timeout,
        attempts=llm_retry_attempts,
        sleep_seconds=llm_retry_sleep_seconds,
        validate=validate,
    )
    content = _extract_chat_completion_content(raw)
    return _parse_visual_review_json(content)


def _extract_chat_completion_content(response_json: Dict[str, Any]) -> str:
    try:
        return norm(response_json["choices"][0]["message"]["content"])
    except Exception:
        pass
    for key in ("content", "text", "output", "answer"):
        if isinstance(response_json.get(key), str):
            return norm(response_json[key])
    data = response_json.get("data")
    if isinstance(data, dict):
        return _extract_chat_completion_content(data)
    return ""


def _parse_visual_review_json(content: str) -> List[Dict[str, Any]]:
    if not content:
        return []
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return []
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        reviews = parsed.get("reviews", [])
        if isinstance(reviews, list):
            return [x for x in reviews if isinstance(x, dict)]
    return []


def _parse_json_object_from_text(content: str) -> Dict[str, Any]:
    """Parse one JSON object from a chat-model response."""
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def modelhub_visual_review_contact_sheet(
    contact_sheet_path: str,
    candidates: List[ImageCandidate],
    context: PageContext,
    model: str,
    endpoint: str,
    logid: str,
    timeout: int = 90,
    llm_retry_attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS,
    llm_retry_sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS,
) -> List[Dict[str, Any]]:
    if not endpoint or not contact_sheet_path or not Path(contact_sheet_path).exists():
        raise RuntimeError("modelhub visual review requires endpoint and an existing contact sheet")
    data = Path(contact_sheet_path).read_bytes()
    image_b64 = base64.b64encode(data).decode("ascii")
    candidate_metadata = [
        {
            "index": idx,
            "filename": urlparse(cand.url).path.rsplit("/", 1)[-1][:120],
            "bucket": cand.bucket,
            "text_density": cand.text_density,
        }
        for idx, cand in enumerate(candidates, 1)
    ]
    prompt_text = (
        "Review this contact sheet for creative video generation. "
        f"Current product: {context.product_name}; brand: {context.brand_name}; slug: {context.page_slug}. "
        "Each tile has a #index. Return strict JSON only, no markdown. "
        "For every index, classify whether it is current product visual, related bundle/add-on, other product, text-heavy reference, logo/UI, or bad asset. "
        "Starter kits, variety packs, accessories, shakers, and bundle images are related_or_bundle; keep them out of main video visuals unless the landing page itself is that bundle/accessory. "
        "Text-heavy nutrition/benefits/instructions/reviews should be copy only, not video visual. "
        "Also extract visible text from each tile when readable. This OCR-like text will be used for selling points/brand/product evidence; do not invent claims that are not visible. "
        "Schema: {\"reviews\":[{\"index\":1,\"visual_bucket\":\"product|lifestyle|ingredient|benefit|how_to_use|spec_or_nutrition|logo|related_or_bundle|other_product|ui_or_icon|text_heavy|bad_asset|unknown\",\"is_current_product\":true,\"text_density\":\"none|low|medium|high|unknown\",\"keep_for_video_generation\":true,\"keep_for_copy_extraction\":false,\"visible_text\":\"short exact readable text, empty if none\",\"key_claims\":[\"claim visible in image\"],\"brand_text\":\"brand text visible, empty if none\",\"product_text\":\"product text visible, empty if none\",\"confidence\":0.9,\"reason\":\"short\"}]} "
        "Candidate metadata: " + json.dumps(candidate_metadata, ensure_ascii=False, separators=(",", ":"))
    )
    payload = {
        "stream": False,
        "model": model,
        "max_tokens": 63000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    if logid:
        headers["X-TT-LOGID"] = logid
    def validate(raw_response: Dict[str, Any]) -> None:
        content = _extract_chat_completion_content(raw_response)
        if not content:
            raise RuntimeError("modelhub visual review returned empty content")
        if not _parse_visual_review_json(content):
            raise RuntimeError("modelhub visual review returned no parseable reviews")

    raw = post_llm_json_with_sleep_retry(
        endpoint,
        headers=headers,
        payload=payload,
        timeout=timeout,
        attempts=llm_retry_attempts,
        sleep_seconds=llm_retry_sleep_seconds,
        validate=validate,
    )
    content = _extract_chat_completion_content(raw)
    return _parse_visual_review_json(content)


def modelhub_visual_review_candidates_batched(
    candidates: List[ImageCandidate],
    context: PageContext,
    model: str,
    endpoint: str,
    logid: str,
    output_dir: Optional[Path],
    case_id: str,
    batch_size: int = 10,
    llm_retry_attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS,
    llm_retry_sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS,
) -> List[Dict[str, Any]]:
    if not output_dir:
        raise RuntimeError("modelhub batched visual review requires an output directory")
    all_reviews: List[Dict[str, Any]] = []
    batch_size = max(1, min(batch_size, 10))
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        batch_id = f"{case_id}_modelhub_batch_{start // batch_size + 1:02d}"
        batch_sheet = create_contact_sheet(batch, batch_id, output_dir, columns=min(5, batch_size), thumb_size=220)
        if not batch_sheet:
            raise RuntimeError(f"failed to create visual review contact sheet for batch {batch_id}")
        reviews = modelhub_visual_review_contact_sheet(
            batch_sheet,
            batch,
            context,
            model,
            endpoint,
            logid,
            llm_retry_attempts=llm_retry_attempts,
            llm_retry_sleep_seconds=llm_retry_sleep_seconds,
        )
        for review in reviews:
            try:
                local_idx = int(review.get("index", 0))
            except Exception:
                continue
            if 1 <= local_idx <= len(batch):
                adjusted = dict(review)
                adjusted["index"] = start + local_idx
                all_reviews.append(adjusted)
    return all_reviews


def bool_from_review(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def list_from_review(value: Any) -> List[str]:
    if isinstance(value, list):
        return [norm(v) for v in value if norm(v)][:12]
    if isinstance(value, str):
        text = norm(value)
        if not text:
            return []
        parts = re.split(r"\s*[\n;|•]+\s*", text)
        return [norm(p) for p in parts if norm(p)][:12]
    return []


def apply_visual_model_reviews(candidates: List[ImageCandidate], reviews: List[Dict[str, Any]]) -> None:
    for review in reviews:
        try:
            idx = int(review.get("index", 0)) - 1
        except Exception:
            continue
        if idx < 0 or idx >= len(candidates):
            continue
        cand = candidates[idx]
        bucket = norm(review.get("visual_bucket"))
        bucket_aliases = {"ingredients": "ingredient", "benefits": "benefit", "instruction": "how_to_use", "instructions": "how_to_use", "nutrition": "spec_or_nutrition", "specs": "spec_or_nutrition", "bundle": "related_or_bundle", "bundles": "related_or_bundle", "starter_kit": "related_or_bundle", "accessory": "related_or_bundle", "accessories": "related_or_bundle"}
        cand.visual_review_bucket = bucket_aliases.get(bucket, bucket) or cand.visual_review_bucket
        cand.text_density = norm(review.get("text_density")) or cand.text_density
        try:
            cand.visual_confidence = float(review.get("confidence") or cand.visual_confidence or 0.0)
        except Exception:
            pass
        is_current = review.get("is_current_product")
        cand.visual_keep_for_video = bool_from_review(review.get("keep_for_video_generation"))
        cand.visual_keep_for_copy = bool_from_review(review.get("keep_for_copy_extraction"))
        if is_current is not None and not bool_from_review(is_current):
            cand.visual_keep_for_video = False
            if cand.visual_review_bucket not in {"text_heavy", "logo", "ui_or_icon", "bad_asset"}:
                cand.visual_review_bucket = "other_product"
        if cand.visual_review_bucket == "related_or_bundle":
            cand.visual_keep_for_video = False
        cand.visual_review_reason = norm(review.get("reason")) or cand.visual_review_reason
        cand.visual_extracted_text = norm(
            review.get("visible_text")
            or review.get("extracted_text")
            or review.get("ocr_text")
            or review.get("image_text")
        )[:1200]
        cand.visual_key_claims = list_from_review(
            review.get("key_claims")
            or review.get("claims")
            or review.get("copy_claims")
        )
        cand.visual_brand_text = norm(review.get("brand_text") or review.get("visible_brand"))[:200]
        cand.visual_product_text = norm(review.get("product_text") or review.get("visible_product"))[:300]
        cand.reason.append("visual_review_model")


def select_creative_ready_images_after_visual_review(
    candidates: List[ImageCandidate],
    max_images: int,
    text_heavy_policy: str,
) -> Dict[str, List[ImageCandidate]]:
    creative: List[ImageCandidate] = []
    text_heavy: List[ImageCandidate] = []
    copy_source: List[ImageCandidate] = []
    rejected: List[ImageCandidate] = []
    logos: List[ImageCandidate] = []
    for cand in sorted(candidates, key=lambda c: (_selection_priority(c), c.visual_confidence, c.score, c.size_score), reverse=True):
        bucket = cand.visual_review_bucket or cand.bucket
        if bucket in {"other_product", "bad_asset", "ui_or_icon", "related_or_bundle"}:
            cand.decision = "drop_other_product" if bucket == "other_product" else "drop_useless"
            rejected.append(cand)
        elif bucket == "logo":
            cand.decision = "keep_logo_only"
            logos.append(cand)
        elif cand.text_density == "high" or bucket == "text_heavy" or (
            cand.text_density in {"medium", "high"} and bucket in {"how_to_use", "benefit", "spec_or_nutrition"}
        ) or (
            cand.text_density == "medium" and (len(cand.visual_extracted_text) > 80 or len(cand.visual_key_claims) >= 2)
        ):
            if text_heavy_policy == "include" and cand.visual_keep_for_video and len(creative) < max_images:
                cand.decision = "keep_visual"
                creative.append(cand)
            elif text_heavy_policy == "exclude":
                cand.decision = "drop_useless"
                rejected.append(cand)
            else:
                cand.decision = "keep_reference"
                text_heavy.append(cand)
                copy_source.append(cand)
        elif cand.visual_keep_for_video and len(creative) < max_images:
            cand.decision = "keep_visual"
            creative.append(cand)
        elif cand.visual_keep_for_copy:
            cand.decision = "keep_reference"
            copy_source.append(cand)
        else:
            cand.decision = "drop_useless"
            rejected.append(cand)
    return {"creative_ready_visual": creative[:max_images], "text_heavy": text_heavy, "copy_source": copy_source, "logos": logos[:1], "rejected": rejected + logos[1:]}

def run_image_funnel(
    images: List[str],
    context: PageContext,
    visual_candidate_limit: int,
    max_creative_images: int,
    text_heavy_policy: str,
    visual_review: str = "none",
    download_visual_candidates_flag: bool = False,
    download_images_dir: Optional[Path] = None,
    case_id: str = "case",
    visual_model: str = "gpt-4o-mini",
    openai_api_key: str = "",
    openai_base_url: str = "https://api.openai.com/v1/chat/completions",
    modelhub_endpoint: str = "",
    modelhub_logid: str = "",
    opaque_shopify_recall_limit: int = 0,
    visual_review_batch_size: int = 10,
    llm_retry_attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS,
    llm_retry_sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS,
) -> Dict[str, Any]:
    warnings: List[str] = []
    raw_count = len(images)
    deduped = dedupe_keep_highest_resolution(images)
    filtered, hard_rejected = hard_reject_images(deduped)
    reviewed: List[ImageCandidate] = []
    for cand in filtered:
        classify_image_by_rules(cand, context)
        score_image_relevance(cand, context)
        reviewed.append(cand)
    visual_candidates = select_visual_candidates_with_recall(reviewed, visual_candidate_limit, opaque_shopify_recall_limit)
    recall_added_count = max(0, len(visual_candidates) - min(visual_candidate_limit, len(reviewed)))
    if recall_added_count:
        warnings.append(f"added {recall_added_count} high-res opaque Shopify images to visual review recall")
    visual_review_status = "skipped"
    contact_sheet_path = ""
    visual_model_reviews: List[Dict[str, Any]] = []
    if visual_review in {"heuristic", "openai", "modelhub", "manual"} or download_visual_candidates_flag:
        if download_images_dir is not None:
            download_visual_candidates(visual_candidates, case_id, download_images_dir)
            contact_sheet_path = create_contact_sheet(visual_candidates, case_id, download_images_dir)
    if visual_review in {"heuristic", "openai", "modelhub", "manual"}:
        if visual_review == "heuristic":
            apply_heuristic_visual_review(visual_candidates, context)
            visual_review_status = "heuristic_completed"
        elif visual_review == "manual":
            visual_review_status = "manual_contact_sheet_ready" if contact_sheet_path else "manual_no_contact_sheet"
        elif visual_review == "openai":
            if not openai_api_key or not contact_sheet_path:
                raise RuntimeError("openai visual review requires API key and downloaded contact sheet")
            visual_model_reviews = openai_visual_review_contact_sheet(
                contact_sheet_path,
                visual_candidates,
                context,
                visual_model,
                openai_api_key,
                openai_base_url,
                llm_retry_attempts=llm_retry_attempts,
                llm_retry_sleep_seconds=llm_retry_sleep_seconds,
            )
            if not visual_model_reviews:
                raise RuntimeError("openai visual review returned no reviews")
            apply_visual_model_reviews(visual_candidates, visual_model_reviews)
            visual_review_status = "openai_completed"
        elif visual_review == "modelhub":
            if not modelhub_endpoint or download_images_dir is None:
                raise RuntimeError("modelhub visual review requires endpoint and download image directory")
            # Use smaller contact-sheet batches by default. A single 30-image
            # sheet can overload the vision prompt and has returned empty/length
            # responses; 10 images per request is steadier and easier to inspect.
            visual_model_reviews = modelhub_visual_review_candidates_batched(
                visual_candidates,
                context,
                visual_model,
                modelhub_endpoint,
                modelhub_logid,
                download_images_dir,
                case_id,
                batch_size=visual_review_batch_size,
                llm_retry_attempts=llm_retry_attempts,
                llm_retry_sleep_seconds=llm_retry_sleep_seconds,
            )
            if not visual_model_reviews:
                raise RuntimeError("modelhub visual review returned no reviews")
            apply_visual_model_reviews(visual_candidates, visual_model_reviews)
            visual_review_status = "modelhub_completed"
    if visual_review in {"heuristic", "openai", "modelhub", "manual"}:
        selected = select_creative_ready_images_after_visual_review(visual_candidates, max_creative_images, text_heavy_policy)
    else:
        selected = select_creative_ready_images(visual_candidates, max_creative_images, text_heavy_policy, context)
    all_rejected = hard_rejected + selected["rejected"]
    if raw_count > max(100, len(deduped) * 3):
        warnings.append("raw image count inflated by responsive/srcset variants")
    if not selected["creative_ready_visual"]:
        warnings.append("no high-confidence creative-ready visual image found")
    if len(selected["creative_ready_visual"]) < 3:
        warnings.append("fewer than 3 creative-ready visual images found")
    if selected["text_heavy"]:
        warnings.append("text-heavy images separated as copy/reference sources")
    if visual_review_status.startswith(("openai_failed", "modelhub_failed")) or "missing_key" in visual_review_status or "missing_endpoint" in visual_review_status or "empty_response" in visual_review_status:
        warnings.append(visual_review_status)
    return {
        "raw_image_count": raw_count,
        "dedup_highres_image_count": len(deduped),
        "filtered_image_count": len(filtered),
        "visual_candidate_count": len(visual_candidates),
        "visual_candidate_base_limit": visual_candidate_limit,
        "opaque_shopify_recall_added_count": recall_added_count,
        "creative_ready_visual_image_count": len(selected["creative_ready_visual"]),
        "text_heavy_image_count": len(selected["text_heavy"]),
        "copy_source_image_count": len(selected["copy_source"]),
        "dedup_highres_images": [c.to_dict() for c in deduped],
        "filtered_images": [c.to_dict() for c in reviewed],
        "visual_candidates": [c.to_dict() for c in visual_candidates],
        "creative_ready_visual_images": [c.to_dict() for c in selected["creative_ready_visual"]],
        "text_heavy_images": [c.to_dict() for c in selected["text_heavy"]],
        "copy_source_images": [c.to_dict() for c in selected["copy_source"]],
        "rejected_images": [c.to_dict() for c in all_rejected],
        "asset_warnings": warnings,
        "visual_review_status": visual_review_status,
        "visual_model": visual_model if visual_review in {"openai", "modelhub"} else visual_review,
        "contact_sheet_path": contact_sheet_path,
        "downloaded_visual_candidate_count": len([c for c in visual_candidates if c.download_status in {"downloaded", "cached"}]),
        "visual_model_reviews": visual_model_reviews,
    }



def _safe_json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def collect_image_text_sources(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collect raw visible text extracted by the visual model.

    This is evidence preservation, not semantic inference: downstream semantic
    fields are generated only by the structured-review LLM.
    """
    sources: List[Dict[str, Any]] = []
    seen = set()
    for item in images:
        url = item.get("url") or item.get("image_url") or ""
        visible_text = norm(item.get("visual_extracted_text") or item.get("visible_text"))
        brand_text = norm(item.get("visual_brand_text") or item.get("brand_text"))
        product_text = norm(item.get("visual_product_text") or item.get("product_text"))
        claims = list_from_review(item.get("visual_key_claims") or item.get("key_claims"))
        if not any([visible_text, brand_text, product_text, claims]):
            continue
        key = (url, visible_text, "|".join(claims))
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "url": url,
            "bucket": item.get("visual_review_bucket") or item.get("bucket") or "unknown",
            "text_density": item.get("text_density") or "unknown",
            "decision": item.get("decision") or "",
            "visible_text": visible_text,
            "key_claims": claims,
            "brand_text": brand_text,
            "product_text": product_text,
            "use_for_copy": bool(visible_text or claims),
        })
    return sources

def _truncate_for_prompt(value: Any, max_chars: int = 24000) -> Any:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return value
    return text[:max_chars] + "...<truncated>"


def _compact_assets_for_llm(items: List[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for item in items[:limit]:
        compact.append({
            "url": item.get("url") or item.get("image_url") or "",
            "source_page_url": item.get("source_page_url", ""),
            "bucket": item.get("visual_review_bucket") or item.get("bucket") or "unknown",
            "text_density": item.get("text_density") or "unknown",
            "decision": item.get("decision") or "",
            "score": item.get("score", item.get("relevance_score", 0)),
            "visual_keep_for_video": item.get("visual_keep_for_video", ""),
            "visual_keep_for_copy": item.get("visual_keep_for_copy", ""),
            "visible_text": item.get("visual_extracted_text") or item.get("visible_text") or "",
            "key_claims": item.get("visual_key_claims") or item.get("key_claims") or [],
            "reason": item.get("reason", [])[:6] if isinstance(item.get("reason", []), list) else item.get("reason", ""),
        })
    return compact


def review_image_asset_item(item: Dict[str, Any], context: PageContext, is_web_asset: bool = False) -> Dict[str, Any]:
    """Normalize an already-reviewed visual asset for structured outputs.

    This function only preserves crawler/vision evidence. It does not infer new
    semantic selling points; downstream semantic fields must come from the LLM
    structured review.
    """
    url = norm(item.get("url") or item.get("image_url"))
    bucket = norm(item.get("visual_review_bucket") or item.get("bucket") or "unknown") or "unknown"
    text_density = norm(item.get("text_density") or "unknown") or "unknown"
    decision = norm(item.get("decision") or "")
    keep_for_video = item.get("visual_keep_for_video")
    if keep_for_video in {"", None}:
        keep_for_video = decision == "keep_visual"
    keep_for_copy = item.get("visual_keep_for_copy")
    if keep_for_copy in {"", None}:
        keep_for_copy = bool(item.get("visual_extracted_text") or item.get("visible_text") or item.get("visual_key_claims") or item.get("key_claims"))
    out = {
        "url": url,
        "source": "web_search" if is_web_asset else "landing_page",
        "source_page_url": norm(item.get("source_page_url")),
        "bucket": bucket,
        "text_density": text_density,
        "decision": decision,
        "use_for_video": bool(keep_for_video),
        "use_for_copy": bool(keep_for_copy),
        "confidence": item.get("visual_confidence", item.get("confidence", 0.0)),
        "visible_text": norm(item.get("visual_extracted_text") or item.get("visible_text"))[:1200],
        "key_claims": list_from_review(item.get("visual_key_claims") or item.get("key_claims")),
        "brand_text": norm(item.get("visual_brand_text") or item.get("brand_text"))[:200],
        "product_text": norm(item.get("visual_product_text") or item.get("product_text"))[:300],
        "reason": item.get("visual_review_reason") or item.get("reason") or "",
    }
    if not out["source_page_url"]:
        out.pop("source_page_url", None)
    if not out["url"]:
        out["warnings"] = ["missing_url"]
    # Include page context as evidence labels only, not as inferred claims.
    out["page_brand_context"] = context.brand_name
    out["page_product_context"] = context.product_name
    return out


def build_structured_review_prompt(result: "CrawlResult", context: PageContext) -> str:
    raw_selling = _safe_json_loads(result.selling_points_json, [])
    web_sources = _safe_json_loads(result.web_search_sources_json, [])
    web_images = _safe_json_loads(result.web_search_image_candidates_json, [])
    creative_images = _safe_json_loads(result.creative_ready_visual_images_json, [])
    text_images = _safe_json_loads(result.text_heavy_images_json, [])
    copy_images = _safe_json_loads(result.copy_source_images_json, [])
    image_text_sources = collect_image_text_sources(creative_images + text_images + copy_images)
    payload = {
        "task": "LLM structured review for a TikTok creative URL crawler. Return strict JSON only.",
        "instructions": [
            "Use evidence from the landing page fields, image OCR-like text, and fetched web-search snippets/pages.",
            "Do not invent or overstate claims. If a claim is not supported, remove it or add a warning.",
            "Determine landing_page_type/objective/business_type/conversion_action from the page evidence. Do not rely on fixed case templates.",
            "Clean selling points: remove navigation/footer/payment/social/noise; keep concise consumer-facing claims with evidence.",
            "Infer pain points, usage scenarios, and creative angles conservatively from supported selling points and page copy.",
            "Review web-search relevance strictly with your own judgment from URL/title/snippet/page/image evidence: no competitors. Same brand and same product are required for use_for_visual; partial matches may be copy-only with warnings. Do not trust crawler recommended_use blindly.",
            "Preserve the dominant landing-page language when writing user-facing copy fields.",
            "Text-heavy/nutrition/instruction visuals are copy references, not main video visuals.",
        ],
        "allowed_enums": {
            "landing_page_type": ["pdp", "plp", "brand_or_landing", "leadgen", "app", "unknown"],
            "objective_guess": ["Website Conversion", "Awareness/Traffic or Catalog", "Leads", "App Install", "Unknown/Needs Review"],
            "business_type": ["ecommerce", "leadgen", "app", "content", "unknown"],
            "conversion_action": ["add_to_cart", "checkout", "purchase", "shop_now", "lead_submit", "install_or_download", "learn_more", "unknown"],
            "match": ["exact", "partial", "none"],
        },
        "page": {
            "input_url": result.input_url,
            "final_url": result.final_url,
            "language": result.language,
            "title_or_product_name": result.product_name,
            "brand_name": result.brand_name,
            "price": result.price,
            "description": result.description,
            "categories": _safe_json_loads(result.category_json, []),
            "ctas": _safe_json_loads(result.cta_json, []),
            "product_list_count": result.product_list_count,
            "page_slug": context.page_slug,
        },
        "raw_candidates": {
            "selling_points_from_html": raw_selling,
            "image_text_sources": image_text_sources,
            "landing_page_visual_assets": _compact_assets_for_llm(creative_images + text_images + copy_images, 45),
            "web_search_sources": web_sources[:40],
            "web_search_image_candidates": _compact_assets_for_llm(web_images, 40),
            "note": "crawler relevance scores are retrieval metadata only; LLM must make final relevance decisions from evidence",
        },
        "required_schema": {
            "landing_page_type": "pdp|plp|brand_or_landing|leadgen|app|unknown",
            "objective_guess": "Website Conversion|Awareness/Traffic or Catalog|Leads|App Install|Unknown/Needs Review",
            "business_type": "ecommerce|leadgen|app|content|unknown",
            "conversion_action": "add_to_cart|checkout|purchase|shop_now|lead_submit|install_or_download|learn_more|unknown",
            "clean_brand_name": "string",
            "clean_product_name": "string",
            "clean_description": "string",
            "clean_selling_points": [{"text": "string", "source": "description|html_li|image_text|web_snippet|web_page", "confidence": 0.0, "evidence": "short evidence"}],
            "clean_target_audience": ["string"],
            "clean_pain_points": ["string"],
            "clean_usage_scenarios": ["string"],
            "clean_creative_angles": [{"angle": "string", "hook": "string", "supporting_points": ["string"], "evidence": ["string"], "confidence": 0.0}],
            "reviewed_web_sources": [{"source_page_url": "string", "title": "string", "snippet": "string", "source_type": "official|retailer|social_search|review|unknown", "brand_match": "exact|partial|none", "product_match": "exact|partial|none", "use_for_copy": True, "use_for_visual": False, "confidence": 0.0, "warnings": []}],
            "reviewed_web_visual_assets": [{"url": "string", "source_page_url": "string", "source": "web_search", "bucket": "product|lifestyle|ingredient|benefit|how_to_use|spec_or_nutrition|logo|related_or_bundle|other_product|ui_or_icon|unknown", "text_density": "none|low|medium|high|unknown", "use_for_video": False, "use_for_copy": True, "confidence": 0.0, "warnings": []}],
            "warnings": ["string"],
        },
    }
    return json.dumps(_truncate_for_prompt(payload), ensure_ascii=False, indent=2)


def call_modelhub_structured_review(
    prompt_text: str,
    model: str,
    endpoint: str,
    logid: str = "",
    timeout: int = 120,
    max_tokens: int = 63000,
    llm_retry_attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS,
    llm_retry_sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS,
) -> Dict[str, Any]:
    if not endpoint:
        raise ValueError("missing structured review ModelHub endpoint")
    payload = {
        "stream": False,
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}]}],
    }
    headers = {"Content-Type": "application/json"}
    if logid:
        headers["X-TT-LOGID"] = logid

    def validate(raw_response: Dict[str, Any]) -> None:
        content = _extract_chat_completion_content(raw_response)
        if not content:
            raise RuntimeError("structured review returned empty content")
        if not _parse_json_object_from_text(content):
            raise RuntimeError("empty_or_unparseable_structured_review_response")

    raw = post_llm_json_with_sleep_retry(
        endpoint,
        headers=headers,
        payload=payload,
        timeout=timeout,
        attempts=llm_retry_attempts,
        sleep_seconds=llm_retry_sleep_seconds,
        validate=validate,
    )
    content = _extract_chat_completion_content(raw)
    parsed = _parse_json_object_from_text(content)
    if not parsed:
        raise ValueError("empty_or_unparseable_structured_review_response")
    parsed["_modelhub_raw_content_preview"] = content[:1000]
    return parsed


def _coerce_str_list(value: Any, limit: int = 12) -> List[str]:
    out: List[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                text = norm(item.get("text") or item.get("point") or item.get("value") or item.get("angle") or item.get("hook"))
            else:
                text = norm(item)
            if text:
                out.append(text)
    elif isinstance(value, str) and norm(value):
        out.append(norm(value))
    return uniq(out)[:limit]


def _coerce_dict_list(value: Any, limit: int = 12) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        out = [x for x in value if isinstance(x, dict)]
    else:
        out = []
    return out[:limit]


def normalize_structured_review(llm_structured: Dict[str, Any], result: "CrawlResult", context: PageContext) -> Dict[str, Any]:
    allowed = {
        "landing_page_type": {"pdp", "plp", "brand_or_landing", "leadgen", "app", "unknown"},
        "objective_guess": {"Website Conversion", "Awareness/Traffic or Catalog", "Leads", "App Install", "Unknown/Needs Review"},
        "business_type": {"ecommerce", "leadgen", "app", "content", "unknown"},
        "conversion_action": {"add_to_cart", "checkout", "purchase", "shop_now", "lead_submit", "install_or_download", "learn_more", "unknown"},
    }
    warnings: List[str] = []
    warnings.extend(_coerce_str_list(llm_structured.get("warnings"), 30))
    warnings.append("structured_review_modelhub")

    clean_point_items = llm_structured.get("clean_selling_points", [])
    clean_points = _coerce_str_list(clean_point_items, 10)
    accepted = _coerce_dict_list(clean_point_items, 20)
    if not clean_points:
        warnings.append("structured_review_missing_clean_selling_points")
        clean_points = _coerce_str_list(_safe_json_loads(result.selling_points_json, []), 10)
    if not accepted:
        warnings.append("structured_review_clean_selling_points_must_include_evidence_objects")

    normalized: Dict[str, Any] = {}
    enum_fallbacks = {
        "landing_page_type": result.landing_page_type or "unknown",
        "objective_guess": result.objective_guess or "Unknown/Needs Review",
        "business_type": result.business_type or "unknown",
        "conversion_action": result.conversion_action or "unknown",
    }
    for key in ["landing_page_type", "objective_guess", "business_type", "conversion_action"]:
        value = norm(llm_structured.get(key))
        if value not in allowed[key]:
            warnings.append(f"structured_review_invalid_or_missing_{key}:{value}")
            value = enum_fallbacks[key]
        normalized[key] = value

    brand = norm(llm_structured.get("clean_brand_name"))
    product = norm(llm_structured.get("clean_product_name"))
    if not brand:
        warnings.append("structured_review_missing_clean_brand_name")
        brand = result.brand_name or context.brand_name
    if not product:
        warnings.append("structured_review_missing_clean_product_name")
        product = result.product_name or context.product_name

    creative_images = _safe_json_loads(result.creative_ready_visual_images_json, [])
    text_images = _safe_json_loads(result.text_heavy_images_json, [])
    copy_images = _safe_json_loads(result.copy_source_images_json, [])
    reviewed_assets: List[Dict[str, Any]] = []
    seen_asset_urls = set()
    for asset in creative_images + text_images + copy_images:
        asset_url = asset.get("url") or asset.get("image_url") or ""
        if asset_url in seen_asset_urls:
            continue
        seen_asset_urls.add(asset_url)
        reviewed_assets.append(review_image_asset_item(asset, context, False))

    normalized.update({
        "clean_brand_name": brand,
        "clean_product_name": product,
        "clean_description": norm(llm_structured.get("clean_description")) or result.description,
        "clean_selling_points": clean_points,
        "accepted_selling_point_candidates": accepted,
        "rejected_selling_point_candidates": _coerce_dict_list(llm_structured.get("rejected_selling_point_candidates"), 30),
        "clean_target_audience": _coerce_str_list(llm_structured.get("clean_target_audience"), 8),
        "clean_pain_points": _coerce_str_list(llm_structured.get("clean_pain_points"), 8),
        "clean_usage_scenarios": _coerce_str_list(llm_structured.get("clean_usage_scenarios"), 8),
        "clean_creative_angles": _coerce_dict_list(llm_structured.get("clean_creative_angles"), 8),
        "reviewed_visual_assets": reviewed_assets,
        "reviewed_web_visual_assets": _coerce_dict_list(llm_structured.get("reviewed_web_visual_assets"), 40),
        "reviewed_web_sources": _coerce_dict_list(llm_structured.get("reviewed_web_sources"), 40),
        "image_text_sources": collect_image_text_sources(creative_images + text_images + copy_images),
        "warnings": uniq([norm(w) for w in warnings if norm(w)]),
        "review_source": "modelhub",
    })
    return normalized


def apply_structured_review_to_result(result: "CrawlResult", structured: Dict[str, Any]) -> None:
    result.structured_review_json = json_dumps(structured)
    for field_name in ["landing_page_type", "objective_guess", "business_type", "conversion_action"]:
        if structured.get(field_name):
            setattr(result, field_name, structured[field_name])
    result.clean_brand_name = structured.get("clean_brand_name", "")
    result.clean_product_name = structured.get("clean_product_name", "")
    result.clean_description = structured.get("clean_description", "")
    result.clean_selling_points_json = json_dumps(structured.get("clean_selling_points", []))
    result.target_audience_guess_json = json_dumps(structured.get("clean_target_audience", []))
    result.clean_pain_points_json = json_dumps(structured.get("clean_pain_points", []))
    result.clean_usage_scenarios_json = json_dumps(structured.get("clean_usage_scenarios", []))
    result.clean_creative_angles_json = json_dumps(structured.get("clean_creative_angles", []))
    result.clean_visual_assets_json = json_dumps(structured.get("reviewed_visual_assets", []) + structured.get("reviewed_web_visual_assets", []))
    result.clean_web_sources_json = json_dumps(structured.get("reviewed_web_sources", []))
    result.image_text_sources_json = json_dumps(structured.get("image_text_sources", []))
    result.clean_asset_warnings_json = json_dumps(structured.get("warnings", []))
    # Keep legacy creative fields aligned with the reviewed LLM output for downstream code paths.
    if structured.get("clean_pain_points"):
        result.pain_points_json = json_dumps(structured.get("clean_pain_points", []))
    if structured.get("clean_usage_scenarios"):
        result.usage_scenarios_json = json_dumps(structured.get("clean_usage_scenarios", []))
    if structured.get("clean_creative_angles"):
        result.creative_angles_json = json_dumps(structured.get("clean_creative_angles", []))


@dataclass
class CrawlOptions:
    image_mode: str = "strict"
    visual_candidate_limit: int = 30
    max_creative_images: int = 12
    text_heavy_policy: str = "separate"
    visual_review: str = "none"
    download_visual_candidates: bool = False
    download_images_dir: str = ""
    visual_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1/chat/completions"
    modelhub_endpoint: str = ""
    modelhub_logid: str = ""
    visual_review_batch_size: int = 10
    opaque_shopify_recall_limit: int = 20
    enable_web_search: bool = True
    web_search_provider: str = "coze"
    web_search_limit: int = 6
    web_image_limit: int = 20
    web_search_fetch_pages: bool = True
    web_fetch_mode: str = "static"
    web_render_limit: int = 3
    web_render_timeout: int = 20
    coze_endpoint: str = "https://api.coze.com/v1/workflow/stream_run"
    coze_workflow_id: str = "7647383585968422965"
    coze_token: str = ""
    structured_review: str = "modelhub"
    structured_review_model: str = "gemini-3.5-flash"
    structured_review_endpoint: str = ""
    structured_review_logid: str = ""
    structured_review_timeout: int = 120
    structured_review_max_tokens: int = 63000
    llm_retry_attempts: int = DEFAULT_LLM_RETRY_ATTEMPTS
    llm_retry_sleep_seconds: float = DEFAULT_LLM_RETRY_SLEEP_SECONDS


@dataclass
class CrawlResult:
    input_url: str
    final_url: str = ""
    redirect_chain: str = ""
    fetch_status: str = ""
    error: str = ""
    landing_page_type: str = ""
    objective_guess: str = ""
    language: str = ""
    brand_name: str = ""
    product_name: str = ""
    price: str = ""
    description: str = ""
    category_json: str = "[]"
    selling_points_json: str = "[]"
    image_count: int = 0
    main_image: str = ""
    images_json: str = "[]"
    video_count: int = 0
    main_video: str = ""
    videos_json: str = "[]"
    cta_json: str = "[]"
    reviews_json: str = "[]"
    product_list_count: int = 0
    business_type: str = ""
    conversion_action: str = ""
    target_audience_guess_json: str = "[]"
    pain_points_json: str = "[]"
    usage_scenarios_json: str = "[]"
    creative_angles_json: str = "[]"
    raw_image_count: int = 0
    dedup_highres_image_count: int = 0
    filtered_image_count: int = 0
    visual_candidate_count: int = 0
    creative_ready_visual_image_count: int = 0
    text_heavy_image_count: int = 0
    copy_source_image_count: int = 0
    dedup_highres_images_json: str = "[]"
    filtered_images_json: str = "[]"
    visual_candidates_json: str = "[]"
    creative_ready_visual_images_json: str = "[]"
    text_heavy_images_json: str = "[]"
    copy_source_images_json: str = "[]"
    rejected_images_json: str = "[]"
    asset_warnings_json: str = "[]"
    visual_review_status: str = ""
    visual_model: str = ""
    contact_sheet_path: str = ""
    downloaded_visual_candidate_count: int = 0
    visual_model_reviews_json: str = "[]"
    web_search_queries_json: str = "[]"
    web_search_sources_json: str = "[]"
    web_search_image_candidates_json: str = "[]"
    web_search_copy_sources_json: str = "[]"
    web_search_warnings_json: str = "[]"
    structured_review_json: str = "{}"
    clean_brand_name: str = ""
    clean_product_name: str = ""
    clean_description: str = ""
    clean_selling_points_json: str = "[]"
    clean_pain_points_json: str = "[]"
    clean_usage_scenarios_json: str = "[]"
    clean_creative_angles_json: str = "[]"
    clean_visual_assets_json: str = "[]"
    clean_web_sources_json: str = "[]"
    image_text_sources_json: str = "[]"
    clean_asset_warnings_json: str = "[]"


def crawl_url(url: str, timeout: int = 20, sleep: float = 0.0, options: Optional[CrawlOptions] = None, case_id: str = "case") -> CrawlResult:
    options = options or CrawlOptions()
    result = CrawlResult(input_url=url)
    if sleep:
        time.sleep(sleep)
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
    except Exception as exc:
        result.error = repr(exc)
        return result

    result.final_url = resp.url
    result.fetch_status = str(resp.status_code)
    result.redirect_chain = " -> ".join([r.url for r in resp.history] + [resp.url])
    if not resp.text:
        return result

    soup = BeautifulSoup(resp.text, "html.parser")
    jsonlds = parse_json_ld(soup)
    product = first_product_jsonld(jsonlds)

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        result.language = norm(html_tag["lang"]).split("-")[0].lower()
    if not result.language:
        result.language = norm(meta_content(soup, "language", "og:locale")).split("_")[0].lower()

    site_name = meta_content(soup, "og:site_name", "application-name")
    title = meta_content(soup, "og:title", "twitter:title") or norm(soup.title.string if soup.title else "")
    h1 = soup.find("h1")

    result.brand_name = extract_brand_from_jsonld(product or {}, jsonlds) or site_name or urlparse(resp.url).netloc.replace("www.", "").split(".")[0]
    if product:
        result.product_name = norm(product.get("name"))
        result.description = norm(product.get("description"))
        cat = product.get("category")
        if isinstance(cat, list):
            categories = [norm(x) for x in cat if norm(x)]
        elif cat:
            categories = [norm(cat)]
        else:
            categories = []
    else:
        result.product_name = norm(h1.get_text(" ")) if h1 else title
        result.description = meta_content(soup, "og:description", "description", "twitter:description")
        categories = []

    if not result.description:
        result.description = meta_content(soup, "og:description", "description", "twitter:description")

    images = extract_images(soup, resp.url, product)
    videos = extract_videos(soup, resp.url, product)
    ctas = extract_ctas(soup)
    result.price = extract_price(soup, product)

    bullets = []
    for li in soup.find_all("li")[:120]:
        text = norm(li.get_text(" "))
        if 5 <= len(text) <= 140:
            bullets.append(text)
    selling_points = uniq(bullets)[:10]

    product_list_count = 0
    for obj in jsonlds:
        if jsonld_type_has(obj, "ItemList"):
            elems = obj.get("itemListElement") or []
            if isinstance(elems, list):
                product_list_count = max(product_list_count, len(elems))

    result.product_list_count = product_list_count
    result.category_json = json_dumps(categories)
    result.selling_points_json = json_dumps(selling_points)
    result.image_count = len(images)
    result.images_json = json_dumps(images)
    result.video_count = len(videos)
    result.main_video = videos[0] if videos else ""
    result.videos_json = json_dumps(videos)
    result.cta_json = json_dumps(ctas)
    result.reviews_json = json_dumps(extract_reviews(product))

    context = build_page_context(result, resp.url, categories)
    candidate_limit = options.visual_candidate_limit
    if options.image_mode == "balanced":
        candidate_limit = max(candidate_limit, 50)
    elif options.image_mode == "recall":
        candidate_limit = max(candidate_limit, 80)
    funnel = run_image_funnel(
        images,
        context,
        candidate_limit,
        options.max_creative_images,
        options.text_heavy_policy,
        visual_review=options.visual_review,
        download_visual_candidates_flag=options.download_visual_candidates,
        download_images_dir=Path(options.download_images_dir) if options.download_images_dir else None,
        case_id=case_id,
        visual_model=options.visual_model,
        openai_api_key=options.openai_api_key,
        openai_base_url=options.openai_base_url,
        modelhub_endpoint=options.modelhub_endpoint,
        modelhub_logid=options.modelhub_logid,
        opaque_shopify_recall_limit=options.opaque_shopify_recall_limit if options.visual_review != "none" else 0,
        visual_review_batch_size=options.visual_review_batch_size,
        llm_retry_attempts=options.llm_retry_attempts,
        llm_retry_sleep_seconds=options.llm_retry_sleep_seconds,
    )
    result.raw_image_count = funnel["raw_image_count"]
    result.dedup_highres_image_count = funnel["dedup_highres_image_count"]
    result.filtered_image_count = funnel["filtered_image_count"]
    result.visual_candidate_count = funnel["visual_candidate_count"]
    result.creative_ready_visual_image_count = funnel["creative_ready_visual_image_count"]
    result.text_heavy_image_count = funnel["text_heavy_image_count"]
    result.copy_source_image_count = funnel["copy_source_image_count"]
    result.dedup_highres_images_json = json_dumps(funnel["dedup_highres_images"])
    result.filtered_images_json = json_dumps(funnel["filtered_images"])
    result.visual_candidates_json = json_dumps(funnel["visual_candidates"])
    result.creative_ready_visual_images_json = json_dumps(funnel["creative_ready_visual_images"])
    result.text_heavy_images_json = json_dumps(funnel["text_heavy_images"])
    result.copy_source_images_json = json_dumps(funnel["copy_source_images"])
    result.rejected_images_json = json_dumps(funnel["rejected_images"])
    result.asset_warnings_json = json_dumps(funnel["asset_warnings"])
    result.visual_review_status = funnel.get("visual_review_status", "")
    result.visual_model = funnel.get("visual_model", "")
    result.contact_sheet_path = funnel.get("contact_sheet_path", "")
    result.downloaded_visual_candidate_count = funnel.get("downloaded_visual_candidate_count", 0)
    result.visual_model_reviews_json = json_dumps(funnel.get("visual_model_reviews", []))

    if options.enable_web_search:
        web_search = run_strict_web_search(
            context,
            resp.url,
            limit=options.web_search_limit,
            image_limit=options.web_image_limit,
            fetch_pages=options.web_search_fetch_pages,
            provider=options.web_search_provider,
            coze_endpoint=options.coze_endpoint,
            coze_workflow_id=options.coze_workflow_id,
            coze_token=options.coze_token,
            fetch_mode=options.web_fetch_mode,
            render_limit=options.web_render_limit,
            render_timeout=options.web_render_timeout,
        )
        result.web_search_queries_json = json_dumps(web_search.get("queries", []))
        result.web_search_sources_json = json_dumps(web_search.get("sources", []))
        result.web_search_image_candidates_json = json_dumps(web_search.get("image_candidates", []))
        result.web_search_copy_sources_json = json_dumps(web_search.get("copy_sources", []))
        result.web_search_warnings_json = json_dumps(web_search.get("warnings", []))

    if options.structured_review == "modelhub":
        prompt_text = build_structured_review_prompt(result, context)
        llm_structured = call_modelhub_structured_review(
            prompt_text,
            model=options.structured_review_model,
            endpoint=options.structured_review_endpoint or options.modelhub_endpoint,
            logid=options.structured_review_logid or options.modelhub_logid,
            timeout=options.structured_review_timeout,
            max_tokens=options.structured_review_max_tokens,
            llm_retry_attempts=options.llm_retry_attempts,
            llm_retry_sleep_seconds=options.llm_retry_sleep_seconds,
        )
        structured = normalize_structured_review(llm_structured, result, context)
        apply_structured_review_to_result(result, structured)
    elif options.structured_review != "none":
        raise ValueError("structured_review must be modelhub or none")

    creative_ready = funnel["creative_ready_visual_images"]
    if creative_ready:
        result.main_image = creative_ready[0]["url"]
    else:
        result.main_image = images[0] if images else ""
    return result


def read_input(path: Path, url_column: str) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, dialect=csv.excel)
        rows = list(reader)
    if not rows:
        return []
    if url_column not in rows[0]:
        raise ValueError(f"URL column {url_column!r} not found. Available columns: {list(rows[0])}")
    return rows


def write_csv(path: Path, records: List[Dict[str, Any]], keep_input_cols: bool) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    for record in records:
        for key in record:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def safe_case_id(record: Dict[str, Any], index: int) -> str:
    case_id = norm(record.get("case_id"))
    if case_id:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)
    url = norm(record.get("input_url") or record.get("raw_url"))
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"row{index}_{digest}"


def write_image_debug_files(output_path: Path, records: List[Dict[str, Any]]) -> None:
    out_dir = output_path.parent
    for idx, record in enumerate(records, 1):
        case = safe_case_id(record, idx)
        debug = {
            "case_id": record.get("case_id", ""),
            "input_url": record.get("input_url", ""),
            "final_url": record.get("final_url", ""),
            "raw_image_count": record.get("raw_image_count", 0),
            "dedup_highres_image_count": record.get("dedup_highres_image_count", 0),
            "filtered_image_count": record.get("filtered_image_count", 0),
            "visual_candidate_count": record.get("visual_candidate_count", 0),
            "dedup_highres_images": json.loads(record.get("dedup_highres_images_json") or "[]"),
            "filtered_images": json.loads(record.get("filtered_images_json") or "[]"),
            "creative_ready_visual_images": json.loads(record.get("creative_ready_visual_images_json") or "[]"),
            "text_heavy_images": json.loads(record.get("text_heavy_images_json") or "[]"),
            "copy_source_images": json.loads(record.get("copy_source_images_json") or "[]"),
            "visual_candidates": json.loads(record.get("visual_candidates_json") or "[]"),
            "rejected_images": json.loads(record.get("rejected_images_json") or "[]"),
            "asset_warnings": json.loads(record.get("asset_warnings_json") or "[]"),
            "visual_review_status": record.get("visual_review_status", ""),
            "visual_model": record.get("visual_model", ""),
            "contact_sheet_path": record.get("contact_sheet_path", ""),
            "downloaded_visual_candidate_count": record.get("downloaded_visual_candidate_count", 0),
            "visual_model_reviews": json.loads(record.get("visual_model_reviews_json") or "[]"),
            "structured_review": json.loads(record.get("structured_review_json") or "{}"),
            "clean_selling_points": json.loads(record.get("clean_selling_points_json") or "[]"),
            "clean_pain_points": json.loads(record.get("clean_pain_points_json") or "[]"),
            "clean_usage_scenarios": json.loads(record.get("clean_usage_scenarios_json") or "[]"),
            "clean_creative_angles": json.loads(record.get("clean_creative_angles_json") or "[]"),
            "clean_web_sources": json.loads(record.get("clean_web_sources_json") or "[]"),
            "clean_asset_warnings": json.loads(record.get("clean_asset_warnings_json") or "[]"),
            "web_search_queries": json.loads(record.get("web_search_queries_json") or "[]"),
            "web_search_sources": json.loads(record.get("web_search_sources_json") or "[]"),
            "web_search_image_candidates": json.loads(record.get("web_search_image_candidates_json") or "[]"),
            "web_search_copy_sources": json.loads(record.get("web_search_copy_sources_json") or "[]"),
            "web_search_warnings": json.loads(record.get("web_search_warnings_json") or "[]"),
        }
        (out_dir / f"{case}_image_funnel_debug.json").write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
        creative = [item["url"] for item in debug["creative_ready_visual_images"]]
        copy_sources = [item["url"] for item in debug["copy_source_images"]]
        (out_dir / f"{case}_creative_ready_images.txt").write_text("\n".join(creative) + ("\n" if creative else ""), encoding="utf-8")
        (out_dir / f"{case}_copy_source_images.txt").write_text("\n".join(copy_sources) + ("\n" if copy_sources else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl landing-page fields and final URLs from a CSV.")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", required=True, help="Output CSV/JSONL path")
    parser.add_argument("--url-column", default="raw_url", help="URL column name, default raw_url")
    parser.add_argument("--format", choices=["csv", "jsonl"], default="csv")
    parser.add_argument("--limit", type=int, default=0, help="Optional max rows")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between requests")
    parser.add_argument("--keep-input-cols", action="store_true", help="Include original CSV columns in output")
    parser.add_argument("--image-mode", choices=["strict", "balanced", "recall"], default="strict")
    parser.add_argument("--visual-candidate-limit", type=int, default=30)
    parser.add_argument("--max-creative-images", type=int, default=12)
    parser.add_argument("--text-heavy-policy", choices=["separate", "exclude", "include"], default="separate")
    parser.add_argument("--write-image-debug-files", action="store_true")
    parser.add_argument("--download-visual-candidates", action="store_true", help="Download visual candidates and generate contact sheets")
    parser.add_argument("--download-images-dir", default="", help="Directory for downloaded candidates/contact sheets; defaults to output_dir/downloaded_visual_candidates")
    parser.add_argument("--visual-review", choices=["none", "heuristic", "openai", "modelhub", "manual"], default="none", help="Optional image-content review mode. openai/modelhub fail loudly if review output is unavailable")
    parser.add_argument("--visual-model", default="gemini-3.5-flash", help="Vision model name for --visual-review modelhub/openai")
    parser.add_argument("--openai-api-key", default="", help="API key for --visual-review openai; defaults to OPENAI_API_KEY env")
    parser.add_argument("--openai-base-url", default="https://api.openai.com/v1/chat/completions", help="OpenAI-compatible chat completions URL")
    parser.add_argument("--modelhub-endpoint", default=default_modelhub_endpoint_from_env(), help="ModelHub crawl endpoint for --visual-review modelhub; defaults to MODELHUB_AK/MODELHUB_ENDPOINT, not AIDP_AK")
    parser.add_argument("--modelhub-logid", default="", help="Optional X-TT-LOGID for --visual-review modelhub")
    parser.add_argument("--visual-review-batch-size", type=int, default=10, help="Images per visual-model contact-sheet request; capped at 10")
    parser.add_argument("--opaque-shopify-recall-limit", type=int, default=20, help="Extra high-res opaque Shopify CDN images to append to visual review beyond --visual-candidate-limit")
    parser.add_argument("--enable-web-search", action="store_true", dest="enable_web_search", default=True, help="Strict same-brand same-product web/social search for supplemental candidates (default: on)")
    parser.add_argument("--no-enable-web-search", "--no-web-search", action="store_false", dest="enable_web_search", help="Disable supplemental web/social search")
    parser.add_argument("--web-search-provider", choices=["coze", "duckduckgo"], default="coze", help="Search backend for web search; Coze uses the provided Google workflow")
    parser.add_argument("--web-search-limit", type=int, default=6, help="Search results per strict query")
    parser.add_argument("--web-image-limit", type=int, default=20, help="Max supplemental web image candidates per URL")
    parser.add_argument("--web-fetch-mode", choices=["static", "rendered", "auto"], default="auto", help="Fetch search result pages by static HTML, Playwright rendered DOM, or auto mode")
    parser.add_argument("--web-render-limit", type=int, default=3, help="Max rendered Playwright fetches per input URL when --web-fetch-mode rendered/auto")
    parser.add_argument("--web-render-timeout", type=int, default=20, help="Timeout seconds for one rendered web fetch")
    parser.add_argument("--no-web-search-fetch-pages", action="store_true", help="Only collect search result URLs; do not fetch result pages for images/snippets")
    parser.add_argument("--coze-endpoint", default="https://api.coze.com/v1/workflow/stream_run", help="Coze stream_run endpoint for --web-search-provider coze")
    parser.add_argument("--coze-workflow-id", default="7647383585968422965", help="Coze workflow_id for Google search")
    parser.add_argument("--coze-token", default="", help="Coze bearer token; defaults to COZE_API_TOKEN env")
    parser.add_argument("--structured-review", choices=["modelhub"], default="modelhub", help="Review and clean structured fields with ModelHub LLM only; fails loudly on invalid output")
    parser.add_argument("--structured-review-model", default=os.environ.get("STRUCTURED_REVIEW_MODEL", "gemini-3.5-flash"), help="ModelHub model for --structured-review modelhub")
    parser.add_argument("--structured-review-endpoint", default=os.environ.get("STRUCTURED_REVIEW_ENDPOINT", default_modelhub_endpoint_from_env()), help="ModelHub endpoint for --structured-review modelhub; defaults to --modelhub-endpoint")
    parser.add_argument("--structured-review-logid", default="", help="Optional X-TT-LOGID for structured review ModelHub call")
    parser.add_argument("--structured-review-timeout", type=int, default=120)
    parser.add_argument("--structured-review-max-tokens", type=int, default=63000)
    parser.add_argument("--llm-retry-attempts", type=int, default=DEFAULT_LLM_RETRY_ATTEMPTS, help="Sleep/retry attempts for every ModelHub/OpenAI-compatible LLM call. Default from LLM_RETRY_ATTEMPTS or 20")
    parser.add_argument("--llm-retry-sleep-seconds", type=float, default=DEFAULT_LLM_RETRY_SLEEP_SECONDS, help="Sleep seconds between LLM retry attempts. Default from LLM_RETRY_SLEEP_SECONDS or 10")
    args = parser.parse_args()

    if args.enable_web_search and args.web_search_provider == "coze" and not (args.coze_token or os.environ.get("COZE_API_TOKEN", "")).strip():
        raise RuntimeError(
            "url_crawl_compare enables Coze web search by default. Export COZE_API_TOKEN, "
            "or pass --no-web-search / --web-search-provider duckduckgo to disable or change the search backend."
        )

    rows = read_input(Path(args.input), args.url_column)
    if args.limit:
        rows = rows[: args.limit]

    out_path = Path(args.output)
    default_download_dir = str(out_path.parent / "downloaded_visual_candidates")
    download_images_dir = args.download_images_dir or (default_download_dir if args.download_visual_candidates or args.visual_review != "none" else "")
    crawl_options = CrawlOptions(
        image_mode=args.image_mode,
        visual_candidate_limit=args.visual_candidate_limit,
        max_creative_images=args.max_creative_images,
        text_heavy_policy=args.text_heavy_policy,
        visual_review=args.visual_review,
        download_visual_candidates=args.download_visual_candidates,
        download_images_dir=download_images_dir,
        visual_model=args.visual_model,
        openai_api_key=args.openai_api_key or __import__("os").environ.get("OPENAI_API_KEY", ""),
        openai_base_url=args.openai_base_url,
        modelhub_endpoint=args.modelhub_endpoint,
        modelhub_logid=args.modelhub_logid,
        visual_review_batch_size=args.visual_review_batch_size,
        opaque_shopify_recall_limit=args.opaque_shopify_recall_limit,
        enable_web_search=args.enable_web_search,
        web_search_provider=args.web_search_provider,
        web_search_limit=args.web_search_limit,
        web_image_limit=args.web_image_limit,
        web_search_fetch_pages=not args.no_web_search_fetch_pages,
        web_fetch_mode=args.web_fetch_mode,
        web_render_limit=args.web_render_limit,
        web_render_timeout=args.web_render_timeout,
        coze_endpoint=args.coze_endpoint,
        coze_workflow_id=args.coze_workflow_id,
        coze_token=args.coze_token or os.environ.get("COZE_API_TOKEN", ""),
        structured_review=args.structured_review,
        structured_review_model=args.structured_review_model,
        structured_review_endpoint=args.structured_review_endpoint,
        structured_review_logid=args.structured_review_logid,
        structured_review_timeout=args.structured_review_timeout,
        structured_review_max_tokens=args.structured_review_max_tokens,
        llm_retry_attempts=args.llm_retry_attempts,
        llm_retry_sleep_seconds=args.llm_retry_sleep_seconds,
    )
    output_records: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        url = row.get(args.url_column, "")
        print(f"[{idx}/{len(rows)}] crawling {url}", file=sys.stderr)
        row_case_id = safe_case_id({**row, "input_url": url}, idx)
        crawled = asdict(crawl_url(url, timeout=args.timeout, sleep=args.sleep, options=crawl_options, case_id=row_case_id))
        record: Dict[str, Any] = {}
        if args.keep_input_cols:
            record.update(row)
        for key in ["advertiser_id", "case_id", args.url_column]:
            if key in row and key not in record:
                record[key] = row[key]
        record.update(crawled)
        output_records.append(record)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "jsonl":
        write_jsonl(out_path, output_records)
    else:
        write_csv(out_path, output_records, args.keep_input_cols)
    if args.write_image_debug_files:
        write_image_debug_files(out_path, output_records)
    print(f"Wrote {len(output_records)} rows to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
