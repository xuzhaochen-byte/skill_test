#!/usr/bin/env python3
"""Resolve advertiser context from Aeolus Ads One Dataset by landing-page URL/domain.

This is the URL-only entrypoint for the benchmark/pattern branch. It first
normalizes the supplied URL to a significant domain, queries rows whose External
URL Domains match that domain, then selects the strongest row by conversions,
clicks, CVR, and impressions. The output shape intentionally matches
``aeolus_adv_context.py`` so downstream benchmark scripts can reuse it.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

APP_ID = 555138
DATASET_ID = 1264389
REGION = "sg"

FIELDS = {
    "p_date": {"id": "10000001976403", "name": "p_date"},
    "advertiser_id": {"id": "10000002542211", "name": "Advertiser ID", "expr": "`advertiser_id`", "roleType": 0, "dataType": "int"},
    "country": {"id": "10000002542230", "name": "Ad Country Code", "expr": "country_code", "roleType": 0, "dataType": "string"},
    "primary_industry": {"id": "10000002542323", "name": "Primary Industry", "expr": "first_industry_name_en", "roleType": 0, "dataType": "string"},
    "secondary_industry": {"id": "10000002542322", "name": "Secondary Industry", "expr": "second_industry_name_en", "roleType": 0, "dataType": "string"},
    "external_url": {"id": "10000002542241", "name": "External Website URL", "expr": "external_url", "roleType": 0, "dataType": "string"},
    "external_domain": {"id": "10000002542312", "name": "External URL Domains", "expr": "cutToFirstSignificantSubdomain(external_url)", "roleType": 0, "dataType": "string"},
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
    "clicks": {"id": "sum_10000002542652", "originId": "10000002542652", "name": "Clicks (Destination)", "expr": "click_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "conversions": {"id": "sum_10000002542653", "originId": "10000002542653", "name": "Conversions", "expr": "convert_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "cvr": {"id": "sum_10000002542683", "originId": "10000002542683", "name": "CVR (Clicks)", "expr": "sum([Conversions])/sum(click_count)", "roleType": 1, "dataType": "float", "aggr": {}},
    "ctr": {"id": "sum_10000002542839", "originId": "10000002542839", "name": "CTR", "expr": "sum(click_count)/sum([Impressions])", "roleType": 1, "dataType": "float", "aggr": {}},
    "impressions": {"id": "sum_10000002542651", "originId": "10000002542651", "name": "Impressions", "expr": "show_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
}

COMMON_TWO_PART_SUFFIXES = {
    "co.uk", "com.au", "com.br", "com.mx", "com.tr", "com.sg", "com.hk", "com.tw", "com.cn", "co.jp", "co.kr",
    "co.id", "co.in", "com.my", "com.ph", "com.vn", "com.ar", "com.co", "com.pe", "com.sa", "com.eg", "com.ua",
}


def unique_id() -> str:
    return f"{int(time.time() * 1000)}-{uuid.uuid4()}"


def parse_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def normalize_row(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() == "NULL":
        return None
    return value


def significant_domain(raw_url: str) -> str:
    text = raw_url.strip()
    if not text:
        raise ValueError("--url is empty")
    if "://" not in text:
        text = "https://" + text
    host = (urlparse(text).hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    host = re.sub(r"^m\.", "", host)
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    suffix2 = ".".join(parts[-2:])
    suffix3 = ".".join(parts[-3:])
    if suffix2 in COMMON_TWO_PART_SUFFIXES and len(parts) >= 3:
        return suffix3
    return suffix2


def add_where(query_where: List[Dict[str, Any]], schema_where: List[Dict[str, Any]], *, field_id: str, name: str, op: str, val: List[Any], role_type: int = 0, data_type: str = "string", val_option: Optional[Dict[str, Any]] = None, partition: bool = False) -> None:
    uid = unique_id()
    option = {"isReportFilter": False, "isWhereInAggr": True, "isDefaultPartitionField": partition}
    val_option = val_option or {}
    query_where.append({"name": name, "id": field_id, "preRelation": "and", "uniqueId": uid, "op": op, "val": val, "valOption": val_option, "option": option})
    schema_where.append({
        "aggrConf": {}, "id": field_id, "originId": field_id, "dimMetId": int(field_id), "dataSetId": DATASET_ID,
        "uniqueId": uid, "highlight": False, "format": {}, "showEditComponent": False, "location": "whereList",
        "preRelation": "and", "name": name, "dataTypeName": data_type, "index": len(schema_where), "roleType": role_type,
        "filter": {"op": op, "val": val, "valOption": val_option, "option": option}, "unremovable": partition,
        "undraggable": False, "isMetric": role_type == 1,
    })


def display_conf(fields_format: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "table", "queryType": "table", "conf": {"version": 33, "lineNumber": False, "measureFirst": False, "transpose": False, "autoWrap": False, "compact": False, "compactDirection": "horizontal", "loadPartialData": True, "pagination": False, "pageSize": 20, "alignDimension": "left", "alignMeasure": "right", "tableStyle": "standard", "display": "standard", "colSpaceMode": "tight", "rowSpaceMode": "loose", "hideHeader": False, "headerBackground": True, "sortable": True}, "enableAdvisor": True, "fieldsFormat": fields_format}


def build_dim_met_list(keys: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
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
        dim_met = {"id": item_id, "originId": origin_id, "dimMetId": int(origin_id), "uniqueId": uid, "name": spec["name"], "expr": spec["expr"], "fullExpr": spec["expr"], "roleType": role_type, "scope": 0, "dataType": spec["dataType"], "isRaw": False, "mapKey": None, "aggregation": aggr, "sourceType": "aggr" if aggr else "raw", "persisted": False, "dataSetId": DATASET_ID}
        dim_met_list.append(dim_met)
        pill = {"uniqueId": uid, "id": item_id, "location": "measures" if role_type == 1 else "dimensions", "dimMetId": int(origin_id), "originId": origin_id, "roleType": role_type, "aggrConf": aggr, "format": {}, "isMetric": role_type == 1, "index": index, "type": spec["dataType"], "isGeoField": False}
        if role_type == 1:
            schema_mets.append(pill)
        else:
            schema_dims.append(pill)
    return dim_met_list, schema_dims, schema_mets, fields_format


def build_body(*, domain: str, country: str, limit: int, min_clicks: int, min_impressions: int, last_sync_days: int) -> Dict[str, Any]:
    keys = [
        "advertiser_id", "country", "primary_industry", "secondary_industry", "external_url", "external_domain",
        "advertiser_name", "brand_name", "account_industry_l0", "account_industry_l1", "account_industry_l2", "account_industry_l3",
        "first_aic", "second_aic", "third_aic", "product_source", "catalog_type",
        "cvr", "conversions", "clicks", "ctr", "impressions",
    ]
    dim_met_list, schema_dims, schema_mets, fields_format = build_dim_met_list(keys)
    dimension_ids = [x["id"] for x in dim_met_list if x["roleType"] == 0]
    measure_ids = [x["id"] for x in dim_met_list if x["roleType"] == 1]
    query_where: List[Dict[str, Any]] = []
    schema_where: List[Dict[str, Any]] = []
    add_where(query_where, schema_where, field_id=FIELDS["p_date"]["id"], name=FIELDS["p_date"]["name"], op="lastSync", val=[last_sync_days], val_option={"datetimeUnit": "day", "anchorOffset": 0}, data_type="date", partition=True)
    add_where(query_where, schema_where, field_id=FIELDS["external_domain"]["id"], name=FIELDS["external_domain"]["name"], op="in", val=[domain])
    if country:
        add_where(query_where, schema_where, field_id=FIELDS["country"]["id"], name=FIELDS["country"]["name"], op="in", val=[country])
    if min_clicks > 0:
        add_where(query_where, schema_where, field_id=FIELDS["clicks"].get("originId", FIELDS["clicks"]["id"]), name=FIELDS["clicks"]["name"], op="ge", val=[min_clicks], role_type=1, data_type="float")
    if min_impressions > 0:
        add_where(query_where, schema_where, field_id=FIELDS["impressions"].get("originId", FIELDS["impressions"]["id"]), name=FIELDS["impressions"]["name"], op="ge", val=[min_impressions], role_type=1, data_type="float")
    conv_uid = next(x["uniqueId"] for x in dim_met_list if x["id"] == FIELDS["conversions"]["id"])
    sort = {"orderByList": [{"id": FIELDS["conversions"]["id"], "order": "desc", "uniqueId": conv_uid}], "orderByListState": [{"id": FIELDS["conversions"]["id"], "order": "desc", "uniqueId": conv_uid}], "type": "sort"}
    display = display_conf(fields_format)
    schema = {"columns": [], "rows": [], "dimensions": schema_dims, "measures": schema_mets, "subMeasures": [], "whereList": schema_where, "colors": [], "sizes": [], "drill": [], "parameters": [], "periodCompare": [], "referenceLine": [], "display": display, "reportFilterConfig": {"structType": "LeftRight", "layoutSize": "Normal"}, "cache": {"enable": True, "expire": None, "cacheVersion": "V1"}, "extensions": {"data": {}, "list": [], "protocolVersion": 1}, "realMetricTableRouteConfig": {"isRealMetricQuery": False}, "whiteList": [], "sort": sort}
    return {"version": 4, "metaData": {"appId": APP_ID}, "dataSourceId": 0, "query": {"dataSetId": DATASET_ID, "dataSetIdList": [DATASET_ID], "fabricBlendingModelInfo": {}, "transform": {"type": "table"}, "groupByIdList": dimension_ids, "selectIdList": dimension_ids + measure_ids, "fillDateTimeList": [], "followFilterRangeList": [], "locations": {"dimensions": dimension_ids, "measures": measure_ids, "rows": [], "columns": [], "tooltips": []}, "dimMetList": dim_met_list, "whereList": query_where, "periodCompare": [], "calculation": {"trendTable": {}}, "limit": limit, "sort": sort, "topN": None, "paramList": [], "cache": {"enable": True, "expire": None, "cacheVersion": "V1"}, "enableNullJoin": False, "hasDynamicField": False, "isFirstScreen": False, "realMetricTableRouteConfig": {"isRealMetricQuery": False}, "fabricModelInfo": {}, "extendQuery": []}, "schema": schema, "display": display, "originalSchema": schema, "switchConf": {"waitForDataReady": 0}}


def run_bytedcli(body_path: Path, limit: int, timeout_ms: int) -> Dict[str, Any]:
    env = os.environ.copy()
    env["BYTEDCLI_CLOUD_SITE"] = env.get("BYTEDCLI_CLOUD_SITE", "i18n")
    cmd = ["bytedcli", "-j", "aeolus", "viz-query", "-r", REGION, "--app-id", str(APP_ID), "--dataset-id", str(DATASET_ID), "--body-file", str(body_path), "--limit", str(limit), "--timeout-ms", str(timeout_ms)]
    proc = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"bytedcli failed with code {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    payload = json.loads(proc.stdout)
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload.get("error") or payload, ensure_ascii=False, indent=2))
    return payload


def records_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    records: List[Dict[str, Any]] = []
    for row in rows:
        rec = {col: normalize_row(val) for col, val in zip(columns, row)}
        if str(rec.get("External Website URL") or "").strip() and str(rec.get("Advertiser ID") or "").strip():
            records.append(rec)
    records.sort(key=lambda r: (
        -(parse_float(r.get("Conversions")) or 0.0),
        -(parse_float(r.get("Clicks (Destination)")) or 0.0),
        -(parse_float(r.get("CVR (Clicks)")) or 0.0),
        -(parse_float(r.get("Impressions")) or 0.0),
    ))
    for i, rec in enumerate(records, 1):
        rec["rank"] = i
    return records


def write_outputs(records: List[Dict[str, Any]], output_dir: Path, url: str, domain: str, source: Dict[str, Any], filters: Dict[str, Any]) -> Tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = records[0] if records else None
    adv_id = str(selected.get("Advertiser ID") or "") if selected else ""
    context = {
        "adv_id": adv_id,
        "input_url": url,
        "input_domain": domain,
        "selection_rule": "best External URL Domains match by conversions, clicks, CVR, impressions",
        "selected": selected,
        "row_count": len(records),
        "source": source,
        "filters": filters,
        "rows": records,
    }
    json_path = output_dir / "adv_context.json"
    csv_path = output_dir / "adv_context_candidates.csv"
    json_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "rank", "Advertiser ID", "Ad Country Code", "Primary Industry", "Secondary Industry", "External Website URL", "External URL Domains",
        "Advertiser Name", "Brand Name (Latest)", "Account Industry Level 0 Name V40 (Latest)",
        "Account Industry Level 1 Name V40 (Latest)", "Account Industry Level 2 Name V40 (Latest)",
        "Account Industry Level 3 Name V40 (Latest)", "First AIC Category Name", "Second AIC Category Name",
        "Third AIC Category Name", "Product Source", "Catalog Type", "CVR (Clicks)", "Conversions",
        "Clicks (Destination)", "CTR", "Impressions",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    env_path = output_dir / "adv_context.env"
    if selected:
        env_path.write_text("\n".join([
            f"ADV_ID={adv_id}",
            f"COUNTRY={selected.get('Ad Country Code','')}",
            f"PRIMARY_INDUSTRY={selected.get('Primary Industry','')}",
            f"SECONDARY_INDUSTRY={selected.get('Secondary Industry','')}",
            f"EXTERNAL_URL={selected.get('External Website URL','')}",
            f"INPUT_URL={url}",
        ]) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"INPUT_URL={url}\nINPUT_DOMAIN={domain}\n", encoding="utf-8")
    return json_path, csv_path, env_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve advertiser landing page/country/industry by URL/domain from Aeolus.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--country", default="", help="Optional country filter if already known")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--min-clicks", type=int, default=1)
    parser.add_argument("--min-impressions", type=int, default=1)
    parser.add_argument("--last-sync-days", type=int, default=30)
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument("--keep-body", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    domain = significant_domain(args.url)
    body = build_body(domain=domain, country=args.country.strip().upper(), limit=args.limit, min_clicks=args.min_clicks, min_impressions=args.min_impressions, last_sync_days=args.last_sync_days)
    body_path = args.output_dir / "aeolus_url_adv_context_body.json"
    body_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = run_bytedcli(body_path, args.limit, args.timeout_ms)
    if not args.keep_body:
        try:
            body_path.unlink()
        except OSError:
            pass
    records = records_from_payload(payload)
    filters = {"url": args.url, "domain": domain, "country": args.country.strip().upper(), "p_date": f"lastSync {args.last_sync_days} day(s)", "min_clicks": args.min_clicks, "min_impressions": args.min_impressions, "external_domain": "exact significant-domain match", "sort": "Conversions desc, Clicks desc, CVR desc"}
    source = {"app_id": APP_ID, "dataset_id": DATASET_ID, "region": REGION, "request_id": (payload.get("data") or {}).get("requestId")}
    json_path, csv_path, env_path = write_outputs(records, args.output_dir, args.url, domain, source, filters)
    if not records:
        raise SystemExit(f"No Aeolus advertiser context found for url={args.url} domain={domain}")
    selected = records[0]
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "env": str(env_path), "row_count": len(records), "selected": selected}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
