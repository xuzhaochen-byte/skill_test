window.BENCHMARK_REPORT_DATA = {
  "input": {
    "adv_id": "7615722443520491521",
    "url": "https://ads.tiktok.com/",
    "country": "FR"
  },
  "data_sources": {
    "adv_data": "/Users/bytedance/test_skill/benchmark_output/7615722443520491521_auto/adv_metrics_for_benchmark.csv",
    "benchmark": "/Users/bytedance/test_skill/benchmark_output/7615722443520491521_auto/dynamic_benchmark_for_report.csv",
    "adv_context": "/Users/bytedance/test_skill/benchmark_output/7615722443520491521_auto/adv_context.json"
  },
  "landing_page": {
    "fetch_status": "not_used_dynamic_benchmark",
    "fetch_error": "",
    "text_excerpt": ""
  },
  "industry_classification": {
    "industry": "Telecommunications-Virtual Network Operators (VNO)",
    "confidence": 1.0,
    "reason": "Industry is taken from the selected Aeolus advertiser primary/secondary industry.",
    "alternative_industries": [],
    "method": "aeolus_context"
  },
  "adv_data_match": {
    "advertiser_id": "7615722443520491521",
    "country_code": "FR",
    "match_note": "exact_adv_id_country",
    "matching_rows": 1.0,
    "raw": {
      "advertiser_id": "7615722443520491521",
      "country_code": "FR",
      "total_cost": "113.05000000000001",
      "ctr": "0.02190405567280817",
      "cvr": "0.2521701388888889",
      "play_3s_ratio": "0.24778761061946902",
      "total_show": "105186.0",
      "total_click": "2304.0",
      "total_convert": "581.0",
      "total_play": "113.0",
      "total_play_3s": "28.0"
    }
  },
  "current_metrics": {
    "ctr": 0.02190405567280817,
    "cvr": 0.2521701388888889,
    "cost": 113.05000000000001,
    "play_3s_ratio": 0.24778761061946902
  },
  "benchmark": {
    "country": "FR",
    "industry": "Telecommunications-Virtual Network Operators (VNO)",
    "support": 5.0,
    "match_note": "exact_dynamic_country_industry",
    "source": "aeolus_dynamic_benchmark",
    "metrics": {
      "ctr": {
        "p10": 0.001746509024943037,
        "p20": 0.002462090214834528,
        "p30": 0.0028265793325724774,
        "p40": 0.0028399763781568853,
        "p50": 0.002853373423741294,
        "p60": 0.004414904570045009,
        "p70": 0.005976435716348725,
        "p80": 0.00891453296142503,
        "p90": 0.01322919630527392
      },
      "cvr": {
        "p10": 0.0,
        "p20": 0.0,
        "p30": 0.00280218475421515,
        "p40": 0.008406554262645454,
        "p50": 0.014010923771075753,
        "p60": 0.03048691095312526,
        "p70": 0.04696289813517476,
        "p80": 0.1441607133809597,
        "p90": 0.32208035669047985
      },
      "cost": {
        "p10": 2.5233000000000003,
        "p20": 3.9566000000000003,
        "p30": 59.790280799999984,
        "p40": 170.02434240000002,
        "p50": 280.258404,
        "p60": 1785.0691124,
        "p70": 3289.8798208000003,
        "p80": 10814.278140000015,
        "p90": 24358.264070000023
      },
      "play_3s_ratio": {
        "p10": 0.03432801388246182,
        "p20": 0.03615715017756897,
        "p30": 0.03798628647267613,
        "p40": 0.04866635955213734,
        "p50": 0.06377190102377558,
        "p60": 0.07887744249541383,
        "p70": 0.10286004067271504,
        "p80": 0.14459675226134217,
        "p90": 0.18633346384996935
      }
    }
  },
  "waterline": {
    "ctr": {
      "label": "CTR",
      "value": 0.02190405567280817,
      "formatted_value": "2.19%",
      "raw_percentile": 95.0,
      "score": 95.0,
      "band": "Top P90+",
      "direction": "higher",
      "interpretation": "CTR is top-tier versus benchmark."
    },
    "cvr": {
      "label": "CVR",
      "value": 0.2521701388888889,
      "formatted_value": "25.22%",
      "raw_percentile": 86.1,
      "score": 86.1,
      "band": "P80-P90",
      "direction": "higher",
      "interpretation": "CVR is strong and above most peers."
    },
    "cost": {
      "label": "Spend",
      "value": 113.05000000000001,
      "formatted_value": "113.1",
      "raw_percentile": 34.8,
      "score": 34.8,
      "band": "P20-P40",
      "direction": "higher",
      "interpretation": "Spend is below median and needs improvement."
    },
    "play_3s_ratio": {
      "label": "3s Play Rate",
      "value": 0.24778761061946902,
      "formatted_value": "24.78%",
      "raw_percentile": 95.0,
      "score": 95.0,
      "band": "Top P90+",
      "direction": "higher",
      "interpretation": "3s Play Rate is top-tier versus benchmark."
    }
  },
  "summary": {
    "overall_score": 77.7,
    "overall": "Overall performance is above the dynamic benchmark middle range, with room to reach top-quartile levels.",
    "strengths": [
      "CTR is top-tier versus benchmark.",
      "CVR is strong and above most peers.",
      "3s Play Rate is top-tier versus benchmark."
    ],
    "risks": [
      "Spend is below median and needs improvement."
    ],
    "recommendations": [
      "Spend scale is below the stronger benchmark range; prioritize scalable creatives and audiences once CTR/CVR are stable."
    ]
  },
  "adv_context": {
    "source": "/Users/bytedance/test_skill/benchmark_output/7615722443520491521_auto/adv_context.json",
    "selection_rule": "highest CVR (Clicks) among non-empty External Website URL rows",
    "selected_external_url": "https://ads.tiktok.com/",
    "selected_country": "FR",
    "selected_primary_industry": "Telecommunications",
    "selected_secondary_industry": "Virtual Network Operators (VNO)",
    "selected_cvr": "0.25",
    "selected_conversions": "576",
    "selected_clicks": "2304"
  }
};
