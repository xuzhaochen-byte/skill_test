#!/usr/bin/env python3
"""Build dynamic benchmark percentiles from Aeolus cohort rows.

The script uses the same Ads One Dataset / viz-query path as reference creative
selection, pulls a country + primary/secondary industry cohort, optionally tightens
by selected advertiser context fields, and computes P10-P90 locally. It writes the
dynamic_benchmark_for_report.csv consumed by benchmark_report.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
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
PERCENTILES = [10, 20, 30, 40, 50, 60, 70, 80, 90]

FIELDS = {
    "p_date": {"id": "10000001976403", "name": "p_date"},
    "advertiser_id": {"id": "10000002542211", "name": "Advertiser ID", "expr": "`advertiser_id`", "roleType": 0, "dataType": "int"},
    "country": {"id": "10000002542230", "name": "Ad Country Code", "expr": "country_code", "roleType": 0, "dataType": "string"},
    "primary_industry": {"id": "10000002542323", "name": "Primary Industry", "expr": "first_industry_name_en", "roleType": 0, "dataType": "string"},
    "secondary_industry": {"id": "10000002542322", "name": "Secondary Industry", "expr": "second_industry_name_en", "roleType": 0, "dataType": "string"},
    "external_url": {"id": "10000002542241", "name": "External Website URL", "expr": "external_url", "roleType": 0, "dataType": "string"},
    "external_domain": {"id": "10000002542312", "name": "External URL Domains", "expr": "cutToFirstSignificantSubdomain(external_url)", "roleType": 0, "dataType": "string"},
    "advertiser_name": {"id": "10000002542210", "name": "Advertiser Name", "expr": "advertiser_name", "roleType": 0, "dataType": "string"},
    "account_industry_l2": {"id": "10000002542638", "name": "Account Industry Level 2 Name V40 (Latest)", "expr": "`account_industry_level_2_name_v40_latest`", "roleType": 0, "dataType": "string"},
    "account_industry_l3": {"id": "10000002542639", "name": "Account Industry Level 3 Name V40 (Latest)", "expr": "`account_industry_level_3_name_v40_latest`", "roleType": 0, "dataType": "string"},
    "second_aic": {"id": "10000002542627", "name": "Second AIC Category Name", "expr": "`second_aic_category_name_en`", "roleType": 0, "dataType": "string"},
    "third_aic": {"id": "10000002542622", "name": "Third AIC Category Name", "expr": "`third_aic_category_name_en`", "roleType": 0, "dataType": "string"},
    "cost": {"id": "sum_10000002542649", "originId": "10000002542649", "name": "Cost (USD)", "expr": "usd_cost", "roleType": 1, "dataType": "float", "aggr": {"exprAggr": "sum("}},
    "impressions": {"id": "sum_10000002542651", "originId": "10000002542651", "name": "Impressions", "expr": "show_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "clicks": {"id": "sum_10000002542652", "originId": "10000002542652", "name": "Clicks (Destination)", "expr": "click_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "conversions": {"id": "sum_10000002542653", "originId": "10000002542653", "name": "Conversions", "expr": "convert_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "video_views": {"id": "sum_10000002542655", "originId": "10000002542655", "name": "Video Views", "expr": "total_play", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "play_3s": {"id": "sum_10000002542697", "originId": "10000002542697", "name": "3-Second Video Views", "expr": "play_duration_3s", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "ctr": {"id": "sum_10000002542681", "originId": "10000002542681", "name": "CTR (Destination)", "expr": "sum(click_count)/sum([Impressions])", "roleType": 1, "dataType": "float", "aggr": {}},
    "cvr": {"id": "sum_10000002542683", "originId": "10000002542683", "name": "CVR (Clicks)", "expr": "sum([Conversions])/sum(click_count)", "roleType": 1, "dataType": "float", "aggr": {}},
}

STRICT_LEVELS = {
    "none": [],
    "account_l3": [("account_industry_l3", "Account Industry Level 3 Name V40 (Latest)")],
    "account_l2_l3": [("account_industry_l2", "Account Industry Level 2 Name V40 (Latest)"), ("account_industry_l3", "Account Industry Level 3 Name V40 (Latest)")],
    "aic3": [("third_aic", "Third AIC Category Name")],
    "aic2_aic3": [("second_aic", "Second AIC Category Name"), ("third_aic", "Third AIC Category Name")],
}


def unique_id() -> str:
    return f"{int(time.time() * 1000)}-{uuid.uuid4()}"


def parse_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        text = str(value).replace(",", "").strip()
        if not text or text.upper() == "NULL":
            return None
        val = float(text)
        return None if math.isnan(val) else val
    except Exception:
        return None


def non_empty(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "NULL":
        return ""
    return text


def normalize_domain(value: Any) -> str:
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


def percentile(values: List[float], pct: int) -> Optional[float]:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * pct / 100.0
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def load_adv_context(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    selected = data.get("selected") or {}
    return {"raw": data, "selected": selected}


def display_conf(fields_format: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"type":"table","queryType":"table","conf":{"version":33,"lineNumber":False,"measureFirst":False,"transpose":False,"autoWrap":False,"compact":False,"compactDirection":"horizontal","loadPartialData":True,"pagination":False,"pageSize":20,"alignDimension":"left","alignMeasure":"right","tableStyle":"standard","display":"standard","colSpaceMode":"tight","rowSpaceMode":"loose","hideHeader":False,"headerBackground":True,"sortable":True},"enableAdvisor":True,"fieldsFormat":fields_format}


def build_dim_met_list(keys: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    dim_met_list=[]; schema_dims=[]; schema_mets=[]; fields_format={}
    for index, key in enumerate(keys):
        spec=FIELDS[key]; item_id=spec["id"]; origin_id=spec.get("originId", item_id); role=int(spec["roleType"]); aggr=spec.get("aggr", {}); uid=unique_id(); fields_format[item_id]={}
        dim_met={"id":item_id,"originId":origin_id,"dimMetId":int(origin_id),"uniqueId":uid,"name":spec["name"],"expr":spec["expr"],"fullExpr":spec["expr"],"roleType":role,"scope":0,"dataType":spec["dataType"],"isRaw":False,"mapKey":None,"aggregation":aggr,"sourceType":"aggr" if aggr else "raw","persisted":False,"dataSetId":DATASET_ID}
        pill={"uniqueId":uid,"id":item_id,"location":"measures" if role==1 else "dimensions","dimMetId":int(origin_id),"originId":origin_id,"roleType":role,"aggrConf":aggr,"format":{},"isMetric":role==1,"index":index,"type":spec["dataType"],"isGeoField":False}
        dim_met_list.append(dim_met); (schema_mets if role==1 else schema_dims).append(pill)
    return dim_met_list, schema_dims, schema_mets, fields_format


def add_where(query_where: List[Dict[str, Any]], schema_where: List[Dict[str, Any]], *, field_id: str, name: str, op: str, val: List[Any], role_type: int = 0, data_type: str = "string", val_option: Optional[Dict[str, Any]] = None, partition: bool = False) -> None:
    uid = unique_id(); val_option = val_option or {}; option = {"isReportFilter": False, "isWhereInAggr": True, "isDefaultPartitionField": partition}
    query_where.append({"name": name, "id": field_id, "preRelation": "and", "uniqueId": uid, "op": op, "val": val, "valOption": val_option, "option": option})
    schema_where.append({"aggrConf": {}, "id": field_id, "originId": field_id, "dimMetId": int(field_id), "dataSetId": DATASET_ID, "uniqueId": uid, "highlight": False, "format": {}, "showEditComponent": False, "location": "whereList", "preRelation": "and", "name": name, "dataTypeName": data_type, "index": len(schema_where), "roleType": role_type, "filter": {"op": op, "val": val, "valOption": val_option, "option": option}, "unremovable": partition, "undraggable": False, "isMetric": role_type == 1})


def strict_filters_from_context(selected: Dict[str, Any], level: str) -> List[Tuple[str, str]]:
    pairs=[]
    for key, column in STRICT_LEVELS.get(level, []):
        value = non_empty(selected.get(column))
        if value:
            pairs.append((key, value))
    return pairs


def build_body(*, country: str, primary: str, secondary: str, selected: Dict[str, Any], strict_match_level: str, last_sync_days: int, min_impressions: int, min_clicks: int, limit: int) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    keys=["advertiser_id","external_domain","advertiser_name","account_industry_l2","account_industry_l3","ctr","cvr","cost","impressions","clicks","conversions","video_views","play_3s"]
    dim_met_list, schema_dims, schema_mets, fields_format = build_dim_met_list(keys)
    dim_ids=[x["id"] for x in dim_met_list if x["roleType"]==0]; met_ids=[x["id"] for x in dim_met_list if x["roleType"]==1]
    qwhere=[]; swhere=[]
    add_where(qwhere, swhere, field_id=FIELDS["p_date"]["id"], name="p_date", op="lastSync", val=[last_sync_days], val_option={"datetimeUnit":"day","anchorOffset":0}, data_type="date", partition=True)
    add_where(qwhere, swhere, field_id=FIELDS["country"]["id"], name=FIELDS["country"]["name"], op="in", val=[country])
    add_where(qwhere, swhere, field_id=FIELDS["primary_industry"]["id"], name=FIELDS["primary_industry"]["name"], op="in", val=[primary])
    add_where(qwhere, swhere, field_id=FIELDS["secondary_industry"]["id"], name=FIELDS["secondary_industry"]["name"], op="in", val=[secondary])
    add_where(qwhere, swhere, field_id=FIELDS["external_url"]["id"], name=FIELDS["external_url"]["name"], op="not in", val=["", " "], data_type="string")
    strict_filters = strict_filters_from_context(selected, strict_match_level)
    for key, value in strict_filters:
        add_where(qwhere, swhere, field_id=FIELDS[key]["id"], name=FIELDS[key]["name"], op="in", val=[value], data_type="string")
    if min_impressions > 0:
        add_where(qwhere, swhere, field_id=FIELDS["impressions"]["originId"], name=FIELDS["impressions"]["name"], op="ge", val=[min_impressions], role_type=1, data_type="float")
    if min_clicks > 0:
        add_where(qwhere, swhere, field_id=FIELDS["clicks"]["originId"], name=FIELDS["clicks"]["name"], op="ge", val=[min_clicks], role_type=1, data_type="float")
    display=display_conf(fields_format)
    schema={"columns":[],"rows":[],"dimensions":schema_dims,"measures":schema_mets,"subMeasures":[],"whereList":swhere,"colors":[],"sizes":[],"drill":[],"parameters":[],"periodCompare":[],"referenceLine":[],"display":display,"reportFilterConfig":{"structType":"LeftRight","layoutSize":"Normal"},"cache":{"enable":True,"expire":None,"cacheVersion":"V1"},"extensions":{"data":{},"list":[],"protocolVersion":1},"realMetricTableRouteConfig":{"isRealMetricQuery":False},"whiteList":[]}
    body={"version":4,"metaData":{"appId":APP_ID},"dataSourceId":0,"query":{"dataSetId":DATASET_ID,"dataSetIdList":[DATASET_ID],"fabricBlendingModelInfo":{},"transform":{"type":"table"},"groupByIdList":dim_ids,"selectIdList":dim_ids+met_ids,"fillDateTimeList":[],"followFilterRangeList":[],"locations":{"dimensions":dim_ids,"measures":met_ids,"rows":[],"columns":[],"tooltips":[]},"dimMetList":dim_met_list,"whereList":qwhere,"periodCompare":[],"calculation":{"trendTable":{}},"limit":limit,"sort":{"type":"sort"},"topN":None,"paramList":[],"cache":{"enable":True,"expire":None,"cacheVersion":"V1"},"enableNullJoin":False,"hasDynamicField":False,"isFirstScreen":False,"realMetricTableRouteConfig":{"isRealMetricQuery":False},"fabricModelInfo":{},"extendQuery":[]},"schema":schema,"display":display,"originalSchema":schema,"switchConf":{"waitForDataReady":0}}
    return body, strict_filters


def run_bytedcli(body_path: Path, limit: int, timeout_ms: int) -> Dict[str, Any]:
    env=os.environ.copy(); env["BYTEDCLI_CLOUD_SITE"]=env.get("BYTEDCLI_CLOUD_SITE", "i18n")
    cmd=["bytedcli","-j","aeolus","viz-query","-r",REGION,"--app-id",str(APP_ID),"--dataset-id",str(DATASET_ID),"--body-file",str(body_path),"--limit",str(limit),"--timeout-ms",str(timeout_ms)]
    proc=subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"bytedcli failed {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    payload=json.loads(proc.stdout)
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload.get("error") or payload, ensure_ascii=False, indent=2))
    return payload


def records_from_payload(payload: Dict[str, Any], *, adv_id: str, selected: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    data=payload.get("data") or {}; cols=data.get("columns") or []; rows=data.get("rows") or []
    selected_domain=normalize_domain(selected.get("External URL Domains") or selected.get("External Website URL"))
    selected_advertiser=non_empty(selected.get("Advertiser Name")).lower()
    included=[]; excluded=[]
    for raw in rows:
        rec={c:v for c,v in zip(cols, raw)}
        rec["_domain_norm"] = normalize_domain(rec.get("External URL Domains"))
        reasons=[]
        if adv_id and str(rec.get("Advertiser ID") or "").strip() == str(adv_id):
            reasons.append("same_adv_id")
        if selected_domain and rec["_domain_norm"] == selected_domain:
            reasons.append("same_domain")
        if selected_advertiser and non_empty(rec.get("Advertiser Name")).lower() == selected_advertiser:
            reasons.append("same_advertiser_name")
        imp=parse_float(rec.get("Impressions")); clk=parse_float(rec.get("Clicks (Destination)")); conv=parse_float(rec.get("Conversions")); cost=parse_float(rec.get("Cost (USD)")); views=parse_float(rec.get("Video Views")); p3=parse_float(rec.get("3-Second Video Views"))
        ctr=parse_float(rec.get("CTR (Destination)"))
        cvr=parse_float(rec.get("CVR (Clicks)"))
        if ctr is None and imp:
            ctr=(clk or 0)/imp
        if cvr is None and clk:
            cvr=(conv or 0)/clk
        play_3s_ratio=(p3 or 0)/views if views else None
        rec["metrics"]={"ctr":ctr,"cvr":cvr,"cost":cost,"play_3s_ratio":play_3s_ratio,"impressions":imp,"clicks":clk,"conversions":conv,"video_views":views,"play_3s":p3}
        if reasons:
            rec["_excluded_reason"] = ";".join(reasons)
            excluded.append(rec)
        else:
            included.append(rec)
    return included, excluded


def build_percentiles(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    out={}
    for metric in ["ctr", "cvr", "cost", "play_3s_ratio"]:
        vals=[r.get("metrics", {}).get(metric) for r in records]
        nums=[v for v in vals if isinstance(v, (int,float)) and math.isfinite(v)]
        out[metric]={f"p{p}": percentile(nums, p) for p in PERCENTILES}
    return out


def write_outputs(*, output_dir: Path, adv_id: str, country: str, primary: str, secondary: str, selected: Dict[str, Any], filters: Dict[str, Any], included: List[Dict[str, Any]], excluded: List[Dict[str, Any]], payload: Dict[str, Any], keep_rows: int) -> Tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics=build_percentiles(included)
    support=len(included)
    warnings=[]
    if support < 10:
        warnings.append("cohort_size_below_10_unstable")
    elif support < 30:
        warnings.append("cohort_size_below_30_use_with_caution")
    result={
        "adv_id": adv_id,
        "source": {"type":"aeolus_dynamic_benchmark", "app_id":APP_ID, "dataset_id":DATASET_ID, "region":REGION, "request_id":(payload.get("data") or {}).get("requestId")},
        "filters": filters,
        "benchmark": {"country": country, "industry": f"{primary}-{secondary}", "support": support, "metrics": metrics, "warnings": warnings},
        "selected_customer": {"external_url": selected.get("External Website URL"), "external_domain": selected.get("External URL Domains"), "advertiser_name": selected.get("Advertiser Name")},
        "cohort_rows": included[:keep_rows],
        "excluded_rows": excluded[:keep_rows],
    }
    json_path=output_dir/"dynamic_benchmark.json"
    csv_path=output_dir/"dynamic_benchmark_cohort.csv"
    report_csv=output_dir/"dynamic_benchmark_for_report.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    cohort_fields=["Advertiser ID","External URL Domains","Advertiser Name","Account Industry Level 2 Name V40 (Latest)","Account Industry Level 3 Name V40 (Latest)","CTR (Destination)","CVR (Clicks)","Cost (USD)","Impressions","Clicks (Destination)","Conversions","Video Views","3-Second Video Views","_domain_norm","_excluded_reason"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=cohort_fields, extrasaction="ignore"); w.writeheader(); w.writerows(included + excluded)
    row={"country_code":country, "industry_category":f"{primary}-{secondary}", "support":support}
    prefix_map={"ctr":"ctr", "cvr":"cvr", "cost":"cost", "play_3s_ratio":"play_3s"}
    for metric, prefix in prefix_map.items():
        for p in PERCENTILES:
            row[f"{prefix}_p{p}"] = metrics.get(metric, {}).get(f"p{p}")
    fields=["country_code","industry_category","support"] + [f"{prefix}_p{p}" for prefix in ["ctr","cvr","play_3s","cost"] for p in PERCENTILES]
    with report_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerow(row)
    return json_path, csv_path, report_csv


def main() -> int:
    ap=argparse.ArgumentParser(description="Build dynamic P10-P90 benchmark from Aeolus cohort rows.")
    ap.add_argument("--adv-id", required=True)
    ap.add_argument("--adv-context", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--country", default="")
    ap.add_argument("--primary-industry", default="")
    ap.add_argument("--secondary-industry", default="")
    ap.add_argument("--strict-match-level", choices=list(STRICT_LEVELS), default="account_l3")
    ap.add_argument("--last-sync-days", type=int, default=30)
    ap.add_argument("--min-impressions", type=int, default=100)
    ap.add_argument("--min-clicks", type=int, default=1)
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--timeout-ms", type=int, default=120000)
    ap.add_argument("--keep-body", action="store_true")
    ap.add_argument("--keep-rows", type=int, default=500)
    args=ap.parse_args()
    ctx=load_adv_context(args.adv_context); selected=ctx["selected"]
    country=args.country or selected.get("Ad Country Code", "")
    primary=args.primary_industry or selected.get("Primary Industry", "")
    secondary=args.secondary_industry or selected.get("Secondary Industry", "")
    if not (country and primary and secondary):
        raise SystemExit("country, primary industry, and secondary industry are required from args or adv_context")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    body, strict_filters = build_body(country=country, primary=primary, secondary=secondary, selected=selected, strict_match_level=args.strict_match_level, last_sync_days=args.last_sync_days, min_impressions=args.min_impressions, min_clicks=args.min_clicks, limit=args.limit)
    body_path=args.output_dir/"aeolus_dynamic_benchmark_body.json"
    body_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    payload=run_bytedcli(body_path, args.limit, args.timeout_ms)
    if not args.keep_body:
        try: body_path.unlink()
        except OSError: pass
    included, excluded = records_from_payload(payload, adv_id=args.adv_id, selected=selected)
    filters={"country":country,"primary_industry":primary,"secondary_industry":secondary,"p_date":f"lastSync {args.last_sync_days} day(s)","min_impressions":args.min_impressions,"min_clicks":args.min_clicks,"require_external_url":True,"strict_match_level":args.strict_match_level,"strict_filters":[{"field":k,"value":v} for k,v in strict_filters],"exclude_same_adv_id":True,"exclude_same_domain":True,"exclude_same_advertiser_name":True,"cohort_grain":"advertiser_id + external_domain + advertiser_name"}
    json_path, csv_path, report_csv = write_outputs(output_dir=args.output_dir, adv_id=args.adv_id, country=country, primary=primary, secondary=secondary, selected=selected, filters=filters, included=included, excluded=excluded, payload=payload, keep_rows=args.keep_rows)
    print(json.dumps({"json":str(json_path),"cohort_csv":str(csv_path),"benchmark_csv":str(report_csv),"included_count":len(included),"excluded_count":len(excluded),"filters":filters}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
