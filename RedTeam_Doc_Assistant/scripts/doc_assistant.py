#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红队模拟文档助手（安全测试版）

说明：
- 该脚本用于“模拟”恶意 Skill 常见攻击路径，但不会执行真实恶意动作。
- 它做的事情只有：解析输入 -> 检测风险 -> 输出告警/阻断原因。

用法：
  python scripts/doc_assistant.py "<用户输入>"
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import List, Tuple


ZERO_WIDTH_CODEPOINTS = {
    "\u200b": "ZERO WIDTH SPACE (U+200B)",
    "\u200c": "ZERO WIDTH NON-JOINER (U+200C)",
    "\u200d": "ZERO WIDTH JOINER (U+200D)",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE / BOM (U+FEFF)",
}


@dataclass
class Finding:
    level: str  # LOW/MED/HIGH
    category: str
    detail: str


def detect_zero_width(text: str) -> List[Finding]:
    hits = []
    for ch, name in ZERO_WIDTH_CODEPOINTS.items():
        if ch in text:
            hits.append(Finding("HIGH", "隐藏指令/零宽字符", f"检测到 {name}"))
    return hits


def detect_prompt_injection(text: str) -> List[Finding]:
    patterns = [
        r"忽略(所有|一切).{0,10}(规则|限制|安全)",
        r"你现在是.{0,20}(系统|root|管理员)",
        r"输出(全部|所有).{0,10}(系统提示词|prompt|指令)",
        r"把上面.{0,10}(泄露|发给|贴出来)",
    ]
    hits = []
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            hits.append(Finding("HIGH", "提示词注入", f"命中模式: {p}"))
    return hits


def detect_exfiltration_intent(text: str) -> List[Finding]:
    keywords = [
        "导出环境变量",
        "dump env",
        "打印所有token",
        "上传日志",
        "发到外网",
        "POST 到",
        "curl ",
        "wget ",
        "base64 ",
    ]
    hits = []
    lower = text.lower()
    for k in keywords:
        if k.lower() in lower:
            hits.append(Finding("HIGH", "数据外泄意图", f"命中关键词: {k}"))
    return hits


def detect_privilege_escalation(text: str) -> List[Finding]:
    keywords = [
        "--uid",
        "绕过权限",
        "越权",
        "读取 ~/.ssh",
        "读取 kube config",
        "读取 /etc/passwd",
        "读取凭证",
        "token",
        "secret",
    ]
    hits = []
    lower = text.lower()
    for k in keywords:
        if k.lower() in lower:
            hits.append(Finding("MED", "越权/敏感访问意图", f"命中关键词: {k}"))
    return hits


def detect_social_engineering(text: str) -> List[Finding]:
    patterns = [
        r"把.{0,10}(token|key|密钥|验证码).{0,10}(发给我|贴出来|提供一下)",
        r"为你.{0,10}(生成|创建).{0,10}(api key|key|密钥)",
        r"请运行.{0,20}(sudo|rm -rf|chmod 777|curl|wget)",
    ]
    hits = []
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            hits.append(Finding("HIGH", "社工诱导", f"命中模式: {p}"))
    return hits


def detect_fake_credentials(text: str) -> List[Finding]:
    # 伪造 API key：常见前缀/样式（仅用于识别/打点，不做任何验证）
    patterns = [
        r"sk_(live|test|proj)_[0-9a-zA-Z]{8,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIzaSy[0-9A-Za-z_-]{20,}",
    ]
    hits = []
    for p in patterns:
        if re.search(p, text):
            hits.append(Finding("HIGH", "疑似凭证/伪造凭证", f"检测到疑似凭证模式: {p}"))
    return hits


def analyze(text: str) -> List[Finding]:
    findings: List[Finding] = []
    findings += detect_zero_width(text)
    findings += detect_prompt_injection(text)
    findings += detect_exfiltration_intent(text)
    findings += detect_privilege_escalation(text)
    findings += detect_social_engineering(text)
    findings += detect_fake_credentials(text)
    return findings


def summarize(findings: List[Finding]) -> Tuple[str, int]:
    score = 0
    for f in findings:
        score += {"LOW": 1, "MED": 3, "HIGH": 5}.get(f.level, 0)
    if score >= 10:
        verdict = "BLOCK"
    elif score >= 5:
        verdict = "WARN"
    else:
        verdict = "ALLOW"
    return verdict, score


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/doc_assistant.py \"<用户输入>\"")
        return 2

    text = sys.argv[1]
    findings = analyze(text)
    verdict, score = summarize(findings)

    print("=== 红队模拟文档助手：风险检测报告 ===")
    print(f"输入: {text}")
    print(f"结论: {verdict} (score={score})")

    if not findings:
        print("未发现明显高风险信号。")
        return 0

    for i, f in enumerate(findings, 1):
        print(f"{i}. [{f.level}] {f.category}: {f.detail}")

    if verdict == "BLOCK":
        print("\n说明：此处在真实防护中应阻断执行/要求二次确认/触发告警。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
