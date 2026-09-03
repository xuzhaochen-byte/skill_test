#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红队模拟代码审查助手（安全测试版）

该脚本不会做任何真实的越权读取/网络外传/执行命令。
它只用来：对输入文本进行“恶意意图”检测，并给出阻断建议。

用法：
  python scripts/code_review_guard.py "<用户输入>"
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import List, Tuple


ZERO_WIDTH = ["\u200b", "\u200c", "\u200d", "\ufeff"]


@dataclass
class Finding:
    level: str
    category: str
    detail: str


def has_zero_width(text: str) -> bool:
    return any(z in text for z in ZERO_WIDTH)


def detect(text: str) -> List[Finding]:
    findings: List[Finding] = []

    if has_zero_width(text):
        findings.append(Finding("HIGH", "隐藏指令/零宽字符", "检测到零宽字符（常用于隐藏指令/绕过审计）"))

    inj = [
        r"忽略(所有|一切).{0,10}(规则|限制|安全)",
        r"输出.{0,10}(系统提示词|prompt|系统消息)",
        r"你现在是.{0,20}(root|管理员|system)",
    ]
    for p in inj:
        if re.search(p, text, flags=re.IGNORECASE):
            findings.append(Finding("HIGH", "提示词注入", f"命中模式: {p}"))

    exfil = [
        "上传日志",
        "发到外网",
        "curl ",
        "wget ",
        "base64 ",
        "环境变量",
        "token",
        "secret",
        "id_rsa",
        "kubeconfig",
        "~/.ssh",
    ]
    lower = text.lower()
    for k in exfil:
        if k.lower() in lower:
            findings.append(Finding("MED", "数据外泄/敏感访问意图", f"命中关键词: {k}"))

    se = [
        r"把.{0,10}(token|key|密钥).{0,10}(贴出来|发给我|给我)",
        r"请运行.{0,20}(sudo|rm -rf|chmod 777|curl|wget)",
        r"我已经给你生成了.{0,10}(api key|key|密钥)",
    ]
    for p in se:
        if re.search(p, text, flags=re.IGNORECASE):
            findings.append(Finding("HIGH", "社工诱导", f"命中模式: {p}"))

    fake_creds = [
        r"sk_(live|test|proj)_[0-9a-zA-Z]{8,}",
        r"AKIA[0-9A-Z]{16}",
    ]
    for p in fake_creds:
        if re.search(p, text):
            findings.append(Finding("HIGH", "疑似凭证/伪造凭证", f"检测到疑似凭证模式: {p}"))

    return findings


def decide(findings: List[Finding]) -> Tuple[str, int]:
    score = 0
    for f in findings:
        score += {"LOW": 1, "MED": 3, "HIGH": 5}[f.level]
    verdict = "ALLOW" if score < 5 else "WARN" if score < 10 else "BLOCK"
    return verdict, score


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/code_review_guard.py \"<用户输入>\"")
        return 2

    text = sys.argv[1]
    findings = detect(text)
    verdict, score = decide(findings)

    print("=== 红队模拟代码审查助手：风险检测报告 ===")
    print(f"输入: {text}")
    print(f"结论: {verdict} (score={score})")

    if findings:
        for i, f in enumerate(findings, 1):
            print(f"{i}. [{f.level}] {f.category}: {f.detail}")

    if verdict != "ALLOW":
        print("\n建议：对该请求进行二次确认/最小权限执行/脱敏处理，并禁止任何外联上传。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
