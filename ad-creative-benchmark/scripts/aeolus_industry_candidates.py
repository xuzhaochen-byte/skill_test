#!/usr/bin/env python3
"""List Aeolus primary/secondary industry candidates for URL-only fallback.

Use this when a landing-page URL cannot be resolved to an advertiser ID. The
script returns recent country/industry pairs with enough delivery support, so an
LLM can classify the URL into one valid Aeolus primary/secondary pair before
querying CTR Top videos.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

APP_ID = 555138
DATASET_ID = 1264389
REGION = "sg"

FIELDS = {
    "p_date": {"id": "10000001976403", "name": "p_date"},
    "country": {"id": "10000002542230", "name": "Ad Country Code", "expr": "country_code", "roleType": 0, "dataType": "string"},
    "primary_industry": {"id": "10000002542323", "name": "Primary Industry", "expr": "first_industry_name_en", "roleType": 0, "dataType": "string"},
    "secondary_industry": {"id": "10000002542322", "name": "Secondary Industry", "expr": "second_industry_name_en", "roleType": 0, "dataType": "string"},
    "clicks": {"id": "sum_10000002542652", "originId": "10000002542652", "name": "Clicks (Destination)", "expr": "click_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "impressions": {"id": "sum_10000002542651", "originId": "10000002542651", "name": "Impressions", "expr": "show_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "conversions": {"id": "sum_10000002542653", "originId": "10000002542653", "name": "Conversions", "expr": "convert_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
}


def unique_id() -> str:
    return f"{int(time.time() * 1000)}-{uuid.uuid4()}"


def parse_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return 0.0


def normalize_row(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() == "NULL":
        return None
    return value


def add_where(query_where: List[Dict[str, Any]], schema_where: List[Dict[str, Any]], *, field_id: str, name: str, op: str, val: List[Any], role_type: int = 0, data_type: str = "string", val_option: Optional[Dict[str, Any]] = None, partition: bool = False) -> None:
    uid = unique_id()
    option = {"isReportFilter": False, "isWhereInAggr": True, "isDefaultPartitionField": partition}
    val_option = val_option or {}
    query_where.append({"name": name, "id": field_id, "preRelation": "and", "uniqueId": uid, "op": op, "val": val, "valOption": val_option, "option": option})
    schema_where.append({"aggrConf": {}, "id": field_id, "originId": field_id, "dimMetId": int(field_id), "dataSetId": DATASET_ID, "uniqueId": uid, "highlight": False, "format": {}, "showEditComponent": False, "location": "whereList", "preRelation": "and", "name": name, "dataTypeName": data_type, "index": len(schema_where), "roleType": role_type, "filter": {"op": op, "val": val, "valOption": val_option, "option": option}, "unremovable": partition, "undraggable": False, "isMetric": role_type == 1})


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


def build_body(*, country: str, limit: int, min_impressions: int, last_sync_days: int) -> Dict[str, Any]:
    keys = ["country", "primary_industry", "secondary_industry", "impressions", "clicks", "conversions"]
    dim_met_list, schema_dims, schema_mets, fields_format = build_dim_met_list(keys)
    dimension_ids = [x["id"] for x in dim_met_list if x["roleType"] == 0]
    measure_ids = [x["id"] for x in dim_met_list if x["roleType"] == 1]
    query_where: List[Dict[str, Any]] = []
    schema_where: List[Dict[str, Any]] = []
    add_where(query_where, schema_where, field_id=FIELDS["p_date"]["id"], name=FIELDS["p_date"]["name"], op="lastSync", val=[last_sync_days], val_option={"datetimeUnit": "day", "anchorOffset": 0}, data_type="date", partition=True)
    if country:
        add_where(query_where, schema_where, field_id=FIELDS["country"]["id"], name=FIELDS["country"]["name"], op="in", val=[country])
    if min_impressions > 0:
        add_where(query_where, schema_where, field_id=FIELDS["impressions"].get("originId", FIELDS["impressions"]["id"]), name=FIELDS["impressions"]["name"], op="ge", val=[min_impressions], role_type=1, data_type="float")
    imp_uid = next(x["uniqueId"] for x in dim_met_list if x["id"] == FIELDS["impressions"]["id"])
    sort = {"orderByList": [{"id": FIELDS["impressions"]["id"], "order": "desc", "uniqueId": imp_uid}], "orderByListState": [{"id": FIELDS["impressions"]["id"], "order": "desc", "uniqueId": imp_uid}], "type": "sort"}
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
        if str(rec.get("Primary Industry") or "").strip() and str(rec.get("Secondary Industry") or "").strip():
            records.append(rec)
    records.sort(key=lambda r: (-parse_float(r.get("Impressions")), -parse_float(r.get("Clicks (Destination)")), -parse_float(r.get("Conversions"))))
    for i, rec in enumerate(records, 1):
        rec["rank"] = i
    return records


def write_outputs(records: List[Dict[str, Any]], output_dir: Path, filters: Dict[str, Any], source: Dict[str, Any]) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "industry_candidates.json"
    csv_path = output_dir / "industry_candidates.csv"
    payload = {"filters": filters, "source": source, "row_count": len(records), "candidates": records}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["rank", "Ad Country Code", "Primary Industry", "Secondary Industry", "Impressions", "Clicks (Destination)", "Conversions"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="List Aeolus primary/secondary industry candidates for URL-only fallback classification.")
    parser.add_argument("--country", default="", help="Optional country filter; if unknown, leave blank and classify country separately or pick from top candidates")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--min-impressions", type=int, default=1000)
    parser.add_argument("--last-sync-days", type=int, default=30)
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument("--keep-body", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    country = args.country.strip().upper()
    body = build_body(country=country, limit=args.limit, min_impressions=args.min_impressions, last_sync_days=args.last_sync_days)
    body_path = args.output_dir / "aeolus_industry_candidates_body.json"
    body_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = run_bytedcli(body_path, args.limit, args.timeout_ms)
    if not args.keep_body:
        try:
            body_path.unlink()
        except OSError:
            pass
    records = records_from_payload(payload)
    filters = {"country": country, "p_date": f"lastSync {args.last_sync_days} day(s)", "min_impressions": args.min_impressions, "sort": "Impressions desc"}
    source = {"app_id": APP_ID, "dataset_id": DATASET_ID, "region": REGION, "request_id": (payload.get("data") or {}).get("requestId")}
    json_path, csv_path = write_outputs(records, args.output_dir, filters, source)
    if not records:
        raise SystemExit("No industry candidates returned from Aeolus")
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "row_count": len(records), "top": records[:5]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
