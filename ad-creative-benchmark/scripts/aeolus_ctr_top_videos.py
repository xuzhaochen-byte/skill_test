#!/usr/bin/env python3
"""Query Aeolus Ads One Dataset for CTR top videos by country and industry.

This script wraps `BYTEDCLI_CLOUD_SITE=i18n bytedcli aeolus viz-query` with the
field IDs and request body shape needed by dataset 1264389.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

APP_ID = 555138
DATASET_ID = 1264389
REGION = "sg"

FIELDS = {
    "p_date": {"id": "10000001976403", "name": "p_date"},
    "advertiser_id": {"id": "10000002542211", "name": "Advertiser ID", "expr": "`advertiser_id`", "roleType": 0, "dataType": "int"},
    "country": {"id": "10000002542230", "name": "Ad Country Code"},
    "primary_industry": {"id": "10000002542323", "name": "Primary Industry"},
    "secondary_industry": {"id": "10000002542322", "name": "Secondary Industry"},
    "video_id": {
        "id": "10000002542392",
        "name": "Video ID",
        "expr": "CAST(video_id as Nullable(String))",
        "roleType": 0,
        "dataType": "string",
    },
    "external_url": {
        "id": "10000002542241",
        "name": "External Website URL",
        "expr": "external_url",
        "roleType": 0,
        "dataType": "string",
    },
    "external_domain": {
        "id": "10000002542312",
        "name": "External URL Domains",
        "expr": "cutToFirstSignificantSubdomain(external_url)",
        "roleType": 0,
        "dataType": "string",
    },
    "advertiser_name": {"id": "10000002542210", "name": "Advertiser Name", "expr": "advertiser_name", "roleType": 0, "dataType": "string"},
    "brand_name": {"id": "10000002542590", "name": "Brand Name (Latest)", "expr": "`brand_name`", "roleType": 0, "dataType": "string"},
    "account_industry_l0": {"id": "10000002542636", "name": "Account Industry Level 0 Name V40 (Latest)", "expr": "`account_industry_level_0_name_v40_latest`", "roleType": 0, "dataType": "string"},
    "account_industry_l1": {"id": "10000002542637", "name": "Account Industry Level 1 Name V40 (Latest)", "expr": "`account_industry_level_1_name_v40_latest`", "roleType": 0, "dataType": "string"},
    "account_industry_l2": {"id": "10000002542638", "name": "Account Industry Level 2 Name V40 (Latest)", "expr": "`account_industry_level_2_name_v40_latest`", "roleType": 0, "dataType": "string"},
    "account_industry_l3": {"id": "10000002542639", "name": "Account Industry Level 3 Name V40 (Latest)", "expr": "`account_industry_level_3_name_v40_latest`", "roleType": 0, "dataType": "string"},
    "first_aic": {"id": "10000002542623", "name": "First AIC Category Name", "expr": "`first_aic_category_name_en`", "roleType": 0, "dataType": "string"},
    "second_aic": {"id": "10000002542627", "name": "Second AIC Category Name", "expr": "`second_aic_category_name_en`", "roleType": 0, "dataType": "string"},
    "third_aic": {"id": "10000002542622", "name": "Third AIC Category Name", "expr": "`third_aic_category_name_en`", "roleType": 0, "dataType": "string"},
    "product_source": {"id": "10000002542416", "name": "Product Source", "expr": "case when product_source = 1 then 'Catalog' when product_source = 2 then 'TikTok Shop' when product_source = 3 then 'Showcase' else toString(product_source) end", "roleType": 0, "dataType": "string"},
    "catalog_type": {"id": "10000002542458", "name": "Catalog Type", "expr": "case when catalog_biz_type = '10' then 'ECOM' when catalog_biz_type = '11' then 'Auto Inventory' when catalog_biz_type = '12' then 'Entertainment' when catalog_biz_type = '13' then 'TravelHotel' when catalog_biz_type = '14' then 'TravelFlight' when catalog_biz_type = '15' then 'TravelDestination' when catalog_biz_type = '16' then 'Auto Model' else catalog_biz_type end", "roleType": 0, "dataType": "string"},
    "video_url": {
        "id": "10000002542396",
        "name": "Video URL",
        "expr": "concat('https://ad-creative-studio-platform.tiktok-row.net/preview?vid=', toString(video_id))",
        "roleType": 0,
        "dataType": "string",
    },
    "ctr": {
        "id": "sum_10000002542839",
        "originId": "10000002542839",
        "name": "CTR",
        "expr": "sum(click_count)/sum([Impressions])",
        "roleType": 1,
        "dataType": "float",
        "aggr": {},
    },
    "impressions": {
        "id": "sum_10000002542651",
        "originId": "10000002542651",
        "name": "Impressions",
        "expr": "show_count",
        "roleType": 1,
        "dataType": "int",
        "aggr": {"exprAggr": "sum("},
    },
    "conversions": {
        "id": "sum_10000002542653",
        "originId": "10000002542653",
        "name": "Conversions",
        "expr": "convert_count",
        "roleType": 1,
        "dataType": "int",
        "aggr": {"exprAggr": "sum("},
    },
    "cvr": {
        "id": "sum_10000002542683",
        "originId": "10000002542683",
        "name": "CVR (Clicks)",
        "expr": "sum([Conversions])/sum(click_count)",
        "roleType": 1,
        "dataType": "float",
        "aggr": {},
    },
    "clicks": {
        "id": "sum_10000002542652",
        "originId": "10000002542652",
        "name": "Clicks (Destination)",
        "expr": "click_count",
        "roleType": 1,
        "dataType": "int",
        "aggr": {"exprAggr": "sum("},
    },
}


def unique_id() -> str:
    return f"{int(time.time() * 1000)}-{uuid.uuid4()}"


def split_benchmark_industry(industry: str) -> Tuple[str, str]:
    if "-" not in industry:
        raise ValueError(
            "Industry must be in benchmark form 'Primary-Secondary', e.g. "
            "'Clothing & Accessories-Ordinary Clothing & Shoes'."
        )
    primary, secondary = industry.split("-", 1)
    primary = primary.strip()
    secondary = secondary.strip()
    if not primary or not secondary:
        raise ValueError(f"Invalid industry: {industry!r}")
    return primary, secondary


def load_industry_from_benchmark_result(path: Path) -> Tuple[str, Optional[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ctx = data.get("adv_context") or {}
    primary = ctx.get("selected_primary_industry")
    secondary = ctx.get("selected_secondary_industry")
    if primary and secondary:
        return f"{primary}-{secondary}", (ctx.get("selected_country") or data.get("input", {}).get("country") or data.get("benchmark", {}).get("country"))
    industry = (
        data.get("industry_classification", {}).get("industry")
        or data.get("benchmark", {}).get("industry")
    )
    country = data.get("input", {}).get("country") or data.get("benchmark", {}).get("country")
    if not industry:
        raise ValueError(f"Could not find industry in {path}")
    return industry, country


def load_context_from_benchmark_result(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    ctx = data.get("adv_context") or {}
    source = ctx.get("source")
    if source:
        try:
            source_path = Path(source)
            if not source_path.is_absolute():
                source_path = Path.cwd() / source_path
            source_data = json.loads(source_path.read_text(encoding="utf-8"))
            selected = source_data.get("selected") or {}
            if selected:
                ctx = {**selected, **ctx}
        except Exception:
            pass
    return ctx


def non_empty_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "NULL":
        return ""
    return text


def strict_filters_from_context(ctx: Dict[str, Any], level: str) -> List[Tuple[str, str]]:
    """Return optional same-field filters from the resolved advertiser context.

    Primary/secondary industry and non-empty URL are always applied elsewhere.
    These extra levels are intentionally incremental: use `account_l3` first,
    then `aic3`, then `domain` only when the user explicitly wants very strict
    competitor/domain matching.
    """
    levels = {
        "none": [],
        "account_l3": [("account_industry_l3", "Account Industry Level 3 Name V40 (Latest)")],
        "account_l2_l3": [
            ("account_industry_l2", "Account Industry Level 2 Name V40 (Latest)"),
            ("account_industry_l3", "Account Industry Level 3 Name V40 (Latest)"),
        ],
        "aic3": [("third_aic", "Third AIC Category Name")],
        "aic2_aic3": [("second_aic", "Second AIC Category Name"), ("third_aic", "Third AIC Category Name")],
        "domain": [("external_domain", "External URL Domains")],
    }
    pairs: List[Tuple[str, str]] = []
    for key, column in levels.get(level, []):
        value = non_empty_text(ctx.get(column))
        if value:
            pairs.append((key, value))
    return pairs


def display_conf(fields_format: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "type": "table",
        "queryType": "table",
        "conf": {
            "version": 33,
            "lineNumber": False,
            "measureFirst": False,
            "transpose": False,
            "autoWrap": False,
            "compact": False,
            "compactDirection": "horizontal",
            "loadPartialData": True,
            "pagination": False,
            "pageSize": 20,
            "alignDimension": "left",
            "alignMeasure": "right",
            "tableStyle": "standard",
            "display": "standard",
            "colSpaceMode": "tight",
            "rowSpaceMode": "loose",
            "hideHeader": False,
            "headerBackground": True,
            "sortable": True,
        },
        "enableAdvisor": True,
        "fieldsFormat": fields_format,
    }


def build_dim_met_list(extra_dimension_keys: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    keys = ["video_id", "external_url", "video_url"] + list(extra_dimension_keys or []) + ["ctr", "impressions", "clicks"]
    dim_met_list: List[Dict[str, Any]] = []
    schema_dims: List[Dict[str, Any]] = []
    schema_mets: List[Dict[str, Any]] = []
    fields_format: Dict[str, Dict[str, Any]] = {}
    for index, key in enumerate(keys):
        spec = FIELDS[key]
        item_id = spec["id"]
        origin_id = spec.get("originId", item_id)
        role_type = int(spec["roleType"])
        aggr = spec.get("aggr", {})
        uid = unique_id()
        fields_format[item_id] = {}
        dim_met = {
            "id": item_id,
            "originId": origin_id,
            "dimMetId": int(origin_id),
            "uniqueId": uid,
            "name": spec["name"],
            "expr": spec["expr"],
            "fullExpr": spec["expr"],
            "roleType": role_type,
            "scope": 0,
            "dataType": spec["dataType"],
            "isRaw": False,
            "mapKey": None,
            "aggregation": aggr,
            "sourceType": "aggr" if aggr else "raw",
            "persisted": False,
            "dataSetId": DATASET_ID,
        }
        dim_met_list.append(dim_met)
        pill = {
            "uniqueId": uid,
            "id": item_id,
            "location": "measures" if role_type == 1 else "dimensions",
            "dimMetId": int(origin_id),
            "originId": origin_id,
            "roleType": role_type,
            "aggrConf": aggr,
            "format": {},
            "isMetric": role_type == 1,
            "index": index,
            "type": spec["dataType"],
            "isGeoField": False,
        }
        if role_type == 1:
            schema_mets.append(pill)
        else:
            schema_dims.append(pill)
    return dim_met_list, schema_dims, schema_mets, fields_format


def add_where(
    query_where: List[Dict[str, Any]],
    schema_where: List[Dict[str, Any]],
    *,
    field_id: str,
    name: str,
    op: str,
    val: List[Any],
    role_type: int = 0,
    data_type: str = "string",
    val_option: Optional[Dict[str, Any]] = None,
    partition: bool = False,
) -> None:
    uid = unique_id()
    option = {"isReportFilter": False, "isWhereInAggr": True, "isDefaultPartitionField": partition}
    val_option = val_option or {}
    query_where.append(
        {
            "name": name,
            "id": field_id,
            "preRelation": "and",
            "uniqueId": uid,
            "op": op,
            "val": val,
            "valOption": val_option,
            "option": option,
        }
    )
    schema_where.append(
        {
            "aggrConf": {},
            "id": field_id,
            "originId": field_id,
            "dimMetId": int(field_id),
            "dataSetId": DATASET_ID,
            "uniqueId": uid,
            "highlight": False,
            "format": {},
            "showEditComponent": False,
            "location": "whereList",
            "preRelation": "and",
            "name": name,
            "dataTypeName": data_type,
            "index": len(schema_where),
            "roleType": role_type,
            "filter": {"op": op, "val": val, "valOption": val_option, "option": option},
            "unremovable": partition,
            "undraggable": False,
            "isMetric": role_type == 1,
        }
    )


def build_body(
    *,
    country: str,
    primary_industry: str,
    secondary_industry: str,
    limit: int,
    min_impressions: int,
    min_clicks: int,
    last_sync_days: int,
    require_external_url: bool = True,
    extra_dimension_keys: Optional[List[str]] = None,
    strict_filters: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    dim_met_list, schema_dims, schema_mets, fields_format = build_dim_met_list(extra_dimension_keys=extra_dimension_keys)
    dimension_ids = [x["id"] for x in dim_met_list if x["roleType"] == 0]
    measure_ids = [x["id"] for x in dim_met_list if x["roleType"] == 1]
    query_where: List[Dict[str, Any]] = []
    schema_where: List[Dict[str, Any]] = []
    add_where(
        query_where,
        schema_where,
        field_id=FIELDS["p_date"]["id"],
        name=FIELDS["p_date"]["name"],
        op="lastSync",
        val=[last_sync_days],
        val_option={"datetimeUnit": "day", "anchorOffset": 0},
        data_type="date",
        partition=True,
    )
    add_where(query_where, schema_where, field_id=FIELDS["country"]["id"], name=FIELDS["country"]["name"], op="in", val=[country])
    add_where(query_where, schema_where, field_id=FIELDS["primary_industry"]["id"], name=FIELDS["primary_industry"]["name"], op="in", val=[primary_industry])
    add_where(query_where, schema_where, field_id=FIELDS["secondary_industry"]["id"], name=FIELDS["secondary_industry"]["name"], op="in", val=[secondary_industry])
    if require_external_url:
        add_where(query_where, schema_where, field_id=FIELDS["external_url"]["id"], name=FIELDS["external_url"]["name"], op="not in", val=["", " "], data_type="string")
    for key, value in strict_filters or []:
        if key in FIELDS and value and str(value).strip():
            add_where(query_where, schema_where, field_id=FIELDS[key]["id"], name=FIELDS[key]["name"], op="in", val=[str(value).strip()], data_type="string")
    if min_impressions > 0:
        add_where(
            query_where,
            schema_where,
            field_id=FIELDS["impressions"]["originId"],
            name=FIELDS["impressions"]["name"],
            op="ge",
            val=[min_impressions],
            role_type=1,
            data_type="float",
        )
    if min_clicks > 0:
        add_where(
            query_where,
            schema_where,
            field_id=FIELDS["clicks"]["originId"],
            name=FIELDS["clicks"]["name"],
            op="ge",
            val=[min_clicks],
            role_type=1,
            data_type="float",
        )

    ctr_uid = next(x["uniqueId"] for x in dim_met_list if x["id"] == FIELDS["ctr"]["id"])
    sort = {
        "orderByList": [{"id": FIELDS["ctr"]["id"], "order": "desc", "uniqueId": ctr_uid}],
        "orderByListState": [{"id": FIELDS["ctr"]["id"], "order": "desc", "uniqueId": ctr_uid}],
        "type": "sort",
    }
    display = display_conf(fields_format)
    schema = {
        "columns": [],
        "rows": [],
        "dimensions": schema_dims,
        "measures": schema_mets,
        "subMeasures": [],
        "whereList": schema_where,
        "colors": [],
        "sizes": [],
        "drill": [],
        "parameters": [],
        "periodCompare": [],
        "referenceLine": [],
        "display": display,
        "reportFilterConfig": {"structType": "LeftRight", "layoutSize": "Normal"},
        "cache": {"enable": True, "expire": None, "cacheVersion": "V1"},
        "extensions": {"data": {}, "list": [], "protocolVersion": 1},
        "realMetricTableRouteConfig": {"isRealMetricQuery": False},
        "whiteList": [],
        "sort": sort,
    }
    body = {
        "version": 4,
        "metaData": {"appId": APP_ID},
        "dataSourceId": 0,
        "query": {
            "dataSetId": DATASET_ID,
            "dataSetIdList": [DATASET_ID],
            "fabricBlendingModelInfo": {},
            "transform": {"type": "table"},
            "groupByIdList": dimension_ids,
            "selectIdList": dimension_ids + measure_ids,
            "fillDateTimeList": [],
            "followFilterRangeList": [],
            "locations": {"dimensions": dimension_ids, "measures": measure_ids, "rows": [], "columns": [], "tooltips": []},
            "dimMetList": dim_met_list,
            "whereList": query_where,
            "periodCompare": [],
            "calculation": {"trendTable": {}},
            "limit": limit,
            "sort": sort,
            "topN": None,
            "paramList": [],
            "cache": {"enable": True, "expire": None, "cacheVersion": "V1"},
            "enableNullJoin": False,
            "hasDynamicField": False,
            "isFirstScreen": False,
            "realMetricTableRouteConfig": {"isRealMetricQuery": False},
            "fabricModelInfo": {},
            "extendQuery": [],
        },
        "schema": schema,
        "display": display,
        "originalSchema": schema,
        "switchConf": {"waitForDataReady": 0},
    }
    return body


def run_bytedcli(body_path: Path, limit: int, timeout_ms: int) -> Dict[str, Any]:
    env = os.environ.copy()
    env["BYTEDCLI_CLOUD_SITE"] = env.get("BYTEDCLI_CLOUD_SITE", "i18n")
    cmd = [
        "bytedcli",
        "-j",
        "aeolus",
        "viz-query",
        "-r",
        REGION,
        "--app-id",
        str(APP_ID),
        "--dataset-id",
        str(DATASET_ID),
        "--body-file",
        str(body_path),
        "--limit",
        str(limit),
        "--timeout-ms",
        str(timeout_ms),
    ]
    proc = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"bytedcli failed with code {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bytedcli returned non-JSON output: {proc.stdout[:1000]}") from exc
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload.get("error") or payload, ensure_ascii=False, indent=2))
    return payload


def normalize_row(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() == "NULL":
        return None
    return value


def has_external_url(rec: Dict[str, Any]) -> bool:
    return bool(str(rec.get("External Website URL") or "").strip())


def write_outputs(payload: Dict[str, Any], output_dir: Path, filters: Dict[str, Any], limit: Optional[int] = None, require_external_url: bool = False) -> Tuple[Path, Path]:
    data = payload.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    records = []
    for row in rows:
        rec = {}
        for col, val in zip(columns, row):
            rec[col] = normalize_row(val)
        if require_external_url and not has_external_url(rec):
            continue
        rec = {"rank": len(records) + 1, **rec}
        records.append(rec)
        if limit and len(records) >= limit:
            break
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "ctr_top50_videos.json"
    csv_path = output_dir / "ctr_top50_videos.csv"
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "app_id": APP_ID,
                    "dataset_id": DATASET_ID,
                    "region": REGION,
                    "request_id": data.get("requestId"),
                },
                "filters": filters,
                "columns": ["rank"] + columns,
                "rows": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["rank"] + columns)
        writer.writeheader()
        writer.writerows(records)
    return json_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query CTR top videos from Aeolus Ads One Dataset.")
    parser.add_argument("--country", required=False, help="Ad delivery country code, e.g. VN")
    parser.add_argument("--industry", help="Benchmark industry in 'Primary-Secondary' form")
    parser.add_argument("--primary-industry", help="Aeolus Primary Industry override")
    parser.add_argument("--secondary-industry", help="Aeolus Secondary Industry override")
    parser.add_argument("--benchmark-result", type=Path, help="benchmark_result.json from benchmark_report.py; used to infer country/industry")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--require-external-url", action="store_true", default=True, help="Require non-empty External Website URL in output. Default: true")
    parser.add_argument("--allow-empty-external-url", dest="require_external_url", action="store_false", help="Allow blank External Website URL rows")
    parser.add_argument("--min-impressions", type=int, default=100)
    parser.add_argument("--min-clicks", type=int, default=1)
    parser.add_argument("--last-sync-days", type=int, default=1)
    parser.add_argument("--strict-match-level", choices=["none", "account_l3", "account_l2_l3", "aic3", "aic2_aic3", "domain"], default="none", help="Optional same-field filters from adv_context after country + primary/secondary industry.")
    parser.add_argument("--include-context-fields", action="store_true", default=True, help="Include domain, advertiser, brand, account-industry, AIC, and product-source fields in output. Default: true")
    parser.add_argument("--minimal-fields", dest="include_context_fields", action="store_false", help="Only output legacy video/url/CTR fields")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_output"))
    parser.add_argument("--keep-body", action="store_true", help="Keep aeolus_ctr_top_videos_body.json in the output dir for debugging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    industry = args.industry
    country = args.country
    if args.benchmark_result:
        loaded_industry, loaded_country = load_industry_from_benchmark_result(args.benchmark_result)
        industry = industry or loaded_industry
        country = country or loaded_country
    if not country:
        raise SystemExit("--country is required unless --benchmark-result contains input.country")
    if args.primary_industry and args.secondary_industry:
        primary, secondary = args.primary_industry, args.secondary_industry
        benchmark_industry = f"{primary}-{secondary}"
    else:
        if not industry:
            raise SystemExit("Provide --industry or both --primary-industry and --secondary-industry")
        primary, secondary = split_benchmark_industry(industry)
        benchmark_industry = industry

    ctx = load_context_from_benchmark_result(args.benchmark_result)
    extra_dimension_keys = []
    if args.include_context_fields:
        extra_dimension_keys = [
            "external_domain", "advertiser_name", "brand_name", "account_industry_l0", "account_industry_l1",
            "account_industry_l2", "account_industry_l3", "first_aic", "second_aic", "third_aic",
            "product_source", "catalog_type",
        ]
    strict_filters = strict_filters_from_context(ctx, args.strict_match_level)
    body = build_body(
        country=country,
        primary_industry=primary,
        secondary_industry=secondary,
        limit=(max(args.limit * 5, 200) if args.require_external_url else args.limit),
        min_impressions=args.min_impressions,
        min_clicks=args.min_clicks,
        last_sync_days=args.last_sync_days,
        require_external_url=args.require_external_url,
        extra_dimension_keys=extra_dimension_keys,
        strict_filters=strict_filters,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    body_path = args.output_dir / "aeolus_ctr_top_videos_body.json"
    body_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    query_limit = max(args.limit * 5, 200) if args.require_external_url else args.limit
    payload = run_bytedcli(body_path, query_limit, args.timeout_ms)
    filters = {
        "country": country,
        "benchmark_industry": benchmark_industry,
        "primary_industry": primary,
        "secondary_industry": secondary,
        "p_date": f"lastSync {args.last_sync_days} day(s)",
        "min_impressions": args.min_impressions,
        "min_clicks": args.min_clicks,
        "sort": "CTR desc",
        "require_external_url": args.require_external_url,
        "strict_match_level": args.strict_match_level,
        "strict_filters": [{"field": key, "value": value} for key, value in strict_filters],
        "context_fields_included": args.include_context_fields,
    }
    json_path, csv_path = write_outputs(payload, args.output_dir, filters, limit=args.limit, require_external_url=args.require_external_url)
    if not args.keep_body:
        try:
            body_path.unlink()
        except OSError:
            pass
    data = payload.get("data") or {}
    print(json.dumps({"row_count": data.get("rowCount"), "json": str(json_path), "csv": str(csv_path), "filters": filters}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
