#!/usr/bin/env python3
"""Resolve advertiser current benchmark metrics from Aeolus Ads One Dataset by adv_id.

Writes a small adv_data-compatible CSV so benchmark_report.py does not depend on
local adv_data.csv. The query keeps 19-digit IDs as strings in JSON filters to
avoid precision loss.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
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
    "advertiser_id": {"id": "10000002542211", "name": "Advertiser ID", "expr": "`advertiser_id`", "roleType": 0, "dataType": "int"},
    "country": {"id": "10000002542230", "name": "Ad Country Code", "expr": "country_code", "roleType": 0, "dataType": "string"},
    "primary_industry": {"id": "10000002542323", "name": "Primary Industry", "expr": "first_industry_name_en", "roleType": 0, "dataType": "string"},
    "secondary_industry": {"id": "10000002542322", "name": "Secondary Industry", "expr": "second_industry_name_en", "roleType": 0, "dataType": "string"},
    "cost": {"id": "sum_10000002542649", "originId": "10000002542649", "name": "Cost (USD)", "expr": "usd_cost", "roleType": 1, "dataType": "float", "aggr": {"exprAggr": "sum("}},
    "impressions": {"id": "sum_10000002542651", "originId": "10000002542651", "name": "Impressions", "expr": "show_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "clicks": {"id": "sum_10000002542652", "originId": "10000002542652", "name": "Clicks (Destination)", "expr": "click_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "conversions": {"id": "sum_10000002542653", "originId": "10000002542653", "name": "Conversions", "expr": "convert_count", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "video_views": {"id": "sum_10000002542655", "originId": "10000002542655", "name": "Video Views", "expr": "total_play", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "play_3s": {"id": "sum_10000002542697", "originId": "10000002542697", "name": "3-Second Video Views", "expr": "play_duration_3s", "roleType": 1, "dataType": "int", "aggr": {"exprAggr": "sum("}},
    "ctr": {"id": "sum_10000002542681", "originId": "10000002542681", "name": "CTR (Destination)", "expr": "sum(click_count)/sum([Impressions])", "roleType": 1, "dataType": "float", "aggr": {}},
    "cvr": {"id": "sum_10000002542683", "originId": "10000002542683", "name": "CVR (Clicks)", "expr": "sum([Conversions])/sum(click_count)", "roleType": 1, "dataType": "float", "aggr": {}},
}


def unique_id() -> str:
    return f"{int(time.time()*1000)}-{uuid.uuid4()}"


def parse_float(v: Any) -> Optional[float]:
    try:
        if v is None or str(v).strip().upper() == "NULL" or str(v).strip() == "":
            return None
        x = float(str(v).replace(",", "").strip())
        return None if math.isnan(x) else x
    except Exception:
        return None


def add_where(query_where: List[Dict[str, Any]], schema_where: List[Dict[str, Any]], *, field_id: str, name: str, op: str, val: List[Any], role_type: int = 0, data_type: str = "string", val_option: Optional[Dict[str, Any]] = None, partition: bool = False) -> None:
    uid = unique_id()
    option = {"isReportFilter": False, "isWhereInAggr": True, "isDefaultPartitionField": partition}
    val_option = val_option or {}
    query_where.append({"name": name, "id": field_id, "preRelation": "and", "uniqueId": uid, "op": op, "val": val, "valOption": val_option, "option": option})
    schema_where.append({"aggrConf": {}, "id": field_id, "originId": field_id, "dimMetId": int(field_id), "dataSetId": DATASET_ID, "uniqueId": uid, "highlight": False, "format": {}, "showEditComponent": False, "location": "whereList", "preRelation": "and", "name": name, "dataTypeName": data_type, "index": len(schema_where), "roleType": role_type, "filter": {"op": op, "val": val, "valOption": val_option, "option": option}, "unremovable": partition, "undraggable": False, "isMetric": role_type == 1})


def display_conf(fields_format: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"type":"table","queryType":"table","conf":{"version":33,"lineNumber":False,"measureFirst":False,"transpose":False,"autoWrap":False,"compact":False,"compactDirection":"horizontal","loadPartialData":True,"pagination":False,"pageSize":20,"alignDimension":"left","alignMeasure":"right","tableStyle":"standard","display":"standard","colSpaceMode":"tight","rowSpaceMode":"loose","hideHeader":False,"headerBackground":True,"sortable":True},"enableAdvisor":True,"fieldsFormat":fields_format}


def build_dim_met_list(keys: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    dim_met_list=[]; schema_dims=[]; schema_mets=[]; fields_format={}
    for index,key in enumerate(keys):
        spec=FIELDS[key]; item_id=spec["id"]; origin_id=spec.get("originId", item_id); role_type=int(spec["roleType"]); aggr=spec.get("aggr", {}); uid=unique_id(); fields_format[item_id]={}
        dim_met={"id":item_id,"originId":origin_id,"dimMetId":int(origin_id),"uniqueId":uid,"name":spec["name"],"expr":spec["expr"],"fullExpr":spec["expr"],"roleType":role_type,"scope":0,"dataType":spec["dataType"],"isRaw":False,"mapKey":None,"aggregation":aggr,"sourceType":"aggr" if aggr else "raw","persisted":False,"dataSetId":DATASET_ID}
        dim_met_list.append(dim_met)
        pill={"uniqueId":uid,"id":item_id,"location":"measures" if role_type==1 else "dimensions","dimMetId":int(origin_id),"originId":origin_id,"roleType":role_type,"aggrConf":aggr,"format":{},"isMetric":role_type==1,"index":index,"type":spec["dataType"],"isGeoField":False}
        (schema_mets if role_type==1 else schema_dims).append(pill)
    return dim_met_list, schema_dims, schema_mets, fields_format


def build_body(*, adv_id: str, country: str, primary: str, secondary: str, last_sync_days: int, limit: int) -> Dict[str, Any]:
    keys=["country","primary_industry","secondary_industry","cost","impressions","clicks","conversions","video_views","play_3s","ctr","cvr"]
    dim_met_list, schema_dims, schema_mets, fields_format = build_dim_met_list(keys)
    dim_ids=[x["id"] for x in dim_met_list if x["roleType"]==0]; met_ids=[x["id"] for x in dim_met_list if x["roleType"]==1]
    query_where=[]; schema_where=[]
    add_where(query_where, schema_where, field_id=FIELDS["p_date"]["id"], name="p_date", op="lastSync", val=[last_sync_days], val_option={"datetimeUnit":"day","anchorOffset":0}, data_type="date", partition=True)
    add_where(query_where, schema_where, field_id=FIELDS["advertiser_id"]["id"], name="Advertiser ID", op="in", val=[str(adv_id)], data_type="float")
    if country:
        add_where(query_where, schema_where, field_id=FIELDS["country"]["id"], name="Ad Country Code", op="in", val=[country])
    if primary:
        add_where(query_where, schema_where, field_id=FIELDS["primary_industry"]["id"], name="Primary Industry", op="in", val=[primary])
    if secondary:
        add_where(query_where, schema_where, field_id=FIELDS["secondary_industry"]["id"], name="Secondary Industry", op="in", val=[secondary])
    display=display_conf(fields_format)
    schema={"columns":[],"rows":[],"dimensions":schema_dims,"measures":schema_mets,"subMeasures":[],"whereList":schema_where,"colors":[],"sizes":[],"drill":[],"parameters":[],"periodCompare":[],"referenceLine":[],"display":display,"reportFilterConfig":{"structType":"LeftRight","layoutSize":"Normal"},"cache":{"enable":True,"expire":None,"cacheVersion":"V1"},"extensions":{"data":{},"list":[],"protocolVersion":1},"realMetricTableRouteConfig":{"isRealMetricQuery":False},"whiteList":[]}
    return {"version":4,"metaData":{"appId":APP_ID},"dataSourceId":0,"query":{"dataSetId":DATASET_ID,"dataSetIdList":[DATASET_ID],"fabricBlendingModelInfo":{},"transform":{"type":"table"},"groupByIdList":dim_ids,"selectIdList":dim_ids+met_ids,"fillDateTimeList":[],"followFilterRangeList":[],"locations":{"dimensions":dim_ids,"measures":met_ids,"rows":[],"columns":[],"tooltips":[]},"dimMetList":dim_met_list,"whereList":query_where,"periodCompare":[],"calculation":{"trendTable":{}},"limit":limit,"sort":{"type":"sort"},"topN":None,"paramList":[],"cache":{"enable":True,"expire":None,"cacheVersion":"V1"},"enableNullJoin":False,"hasDynamicField":False,"isFirstScreen":False,"realMetricTableRouteConfig":{"isRealMetricQuery":False},"fabricModelInfo":{},"extendQuery":[]},"schema":schema,"display":display,"originalSchema":schema,"switchConf":{"waitForDataReady":0}}


def run_bytedcli(body_path: Path, limit: int, timeout_ms: int) -> Dict[str, Any]:
    env=os.environ.copy(); env["BYTEDCLI_CLOUD_SITE"]=env.get("BYTEDCLI_CLOUD_SITE","i18n")
    cmd=["bytedcli","-j","aeolus","viz-query","-r",REGION,"--app-id",str(APP_ID),"--dataset-id",str(DATASET_ID),"--body-file",str(body_path),"--limit",str(limit),"--timeout-ms",str(timeout_ms)]
    proc=subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"bytedcli failed {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    payload=json.loads(proc.stdout)
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload.get("error") or payload, ensure_ascii=False, indent=2))
    return payload


def record_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    data=payload.get("data") or {}; cols=data.get("columns") or []; rows=data.get("rows") or []
    if not rows:
        raise SystemExit("No Aeolus metric rows found for adv_id/current filters")
    rec={c:v for c,v in zip(cols, rows[0])}
    cost=parse_float(rec.get("Cost (USD)")); imp=parse_float(rec.get("Impressions")); clk=parse_float(rec.get("Clicks (Destination)")); conv=parse_float(rec.get("Conversions")); views=parse_float(rec.get("Video Views")); p3=parse_float(rec.get("3-Second Video Views"))
    ctr=parse_float(rec.get("CTR (Destination)")); cvr=parse_float(rec.get("CVR (Clicks)"))
    if ctr is None and imp: ctr=(clk or 0)/imp
    if cvr is None and clk: cvr=(conv or 0)/clk
    play_3s_ratio=(p3 or 0)/(views or 0) if views else None
    rec["_derived"]={"total_cost":cost,"ctr":ctr,"cvr":cvr,"play_3s_ratio":play_3s_ratio,"total_show":imp,"total_click":clk,"total_convert":conv,"total_play":views,"total_play_3s":p3}
    return rec


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--adv-id", required=True); ap.add_argument("--country", default=""); ap.add_argument("--primary-industry", default=""); ap.add_argument("--secondary-industry", default="")
    ap.add_argument("--adv-context", type=Path); ap.add_argument("--output-dir", type=Path, required=True); ap.add_argument("--last-sync-days", type=int, default=30); ap.add_argument("--limit", type=int, default=20); ap.add_argument("--timeout-ms", type=int, default=120000); ap.add_argument("--keep-body", action="store_true")
    args=ap.parse_args()
    if args.adv_context:
        ctx=json.loads(args.adv_context.read_text(encoding="utf-8")); sel=ctx.get("selected") or {}
        args.country=args.country or sel.get("Ad Country Code", ""); args.primary_industry=args.primary_industry or sel.get("Primary Industry", ""); args.secondary_industry=args.secondary_industry or sel.get("Secondary Industry", "")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    body=build_body(adv_id=args.adv_id, country=args.country, primary=args.primary_industry, secondary=args.secondary_industry, last_sync_days=args.last_sync_days, limit=args.limit)
    body_path=args.output_dir/"aeolus_adv_metrics_body.json"; body_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    payload=run_bytedcli(body_path, args.limit, args.timeout_ms)
    if not args.keep_body:
        try: body_path.unlink()
        except OSError: pass
    rec=record_from_payload(payload)
    out_json=args.output_dir/"adv_metrics.json"; out_csv=args.output_dir/"adv_metrics_for_benchmark.csv"
    result={"adv_id":args.adv_id,"filters":{"country":args.country,"primary_industry":args.primary_industry,"secondary_industry":args.secondary_industry,"p_date":f"lastSync {args.last_sync_days} day(s)"},"row":rec,"source":{"app_id":APP_ID,"dataset_id":DATASET_ID,"region":REGION,"request_id":(payload.get("data") or {}).get("requestId")}}
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    d=rec["_derived"]
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        fields=["advertiser_id","country_code","total_cost","ctr","cvr","play_3s_ratio","total_show","total_click","total_convert","total_play","total_play_3s"]
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerow({"advertiser_id":args.adv_id,"country_code":args.country,"total_cost":d["total_cost"],"ctr":d["ctr"],"cvr":d["cvr"],"play_3s_ratio":d["play_3s_ratio"],"total_show":d["total_show"],"total_click":d["total_click"],"total_convert":d["total_convert"],"total_play":d["total_play"],"total_play_3s":d["total_play_3s"]})
    print(json.dumps({"json":str(out_json),"csv":str(out_csv),"metrics":d,"raw":{k:v for k,v in rec.items() if k != "_derived"}}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
