#!/usr/bin/env python3
"""从会议纪要 Markdown 中提取「待办事项」表格，输出为 CSV。

用法:
    python3 scripts/extract_action_items.py <纪要文件.md> [-o output.csv]

解析规则:
    - 定位包含「待办事项」的二级标题（## ...待办事项...）
    - 读取其后的 Markdown 表格（以 | 开头的行）
    - 跳过表头行和分隔行（|---|---|）
    - 期望列顺序: # | 待办动作 | 负责人 | 截止时间 | 状态
      若列数不足则按可用列尽量填充，缺失补空。
输出:
    与输入文件同目录、同名的 <名>_action_items.csv（除非用 -o 指定）。
"""
import argparse
import csv
import os
import re
import sys

HEADER = ["序号", "待办动作", "负责人", "截止时间", "状态"]


def parse_table_rows(lines, start_idx):
    """从 start_idx 开始收集连续的表格行，返回 (rows, next_idx)。"""
    rows = []
    i = start_idx
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|"):
            rows.append(line)
            i += 1
        elif line == "":
            # 允许表格前的空行，但表格开始后遇到空行即结束
            if rows:
                break
            i += 1
        else:
            if rows:
                break
            i += 1
    return rows, i


def split_row(row):
    """把 Markdown 表格行拆成单元格列表。"""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells


def is_separator(cells):
    return all(re.fullmatch(r":?-{2,}:?", c or "-") or set(c) <= {"-", ":", " "} and c for c in cells) if cells else False


def extract(md_path):
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 找到「待办事项」标题
    section_idx = None
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("#") and "待办" in line:
            section_idx = idx + 1
            break
    if section_idx is None:
        print("⚠️  未找到「待办事项」章节，未生成 CSV。", file=sys.stderr)
        return None

    raw_rows, _ = parse_table_rows(lines, section_idx)
    if not raw_rows:
        print("⚠️  「待办事项」章节下未找到表格。", file=sys.stderr)
        return None

    parsed = []
    for r in raw_rows:
        cells = split_row(r)
        # 跳过分隔行
        if cells and all(set(c) <= {"-", ":", " "} for c in cells if c):
            if any(c for c in cells):
                continue
        parsed.append(cells)

    if not parsed:
        return None

    # 第一行通常是表头，若含「待办」「动作」等字样则跳过
    data_rows = parsed
    first = "".join(parsed[0])
    if any(k in first for k in ("待办", "动作", "负责人", "状态")):
        data_rows = parsed[1:]

    result = []
    for cells in data_rows:
        # 归一到 5 列
        cells = (cells + [""] * 5)[:5]
        # 若第一列不是序号（空或非数字），仍原样保留
        result.append(cells)
    return result


def main():
    ap = argparse.ArgumentParser(description="从会议纪要 Markdown 提取待办事项为 CSV")
    ap.add_argument("md_file", help="会议纪要 Markdown 文件路径")
    ap.add_argument("-o", "--output", help="输出 CSV 路径")
    args = ap.parse_args()

    if not os.path.isfile(args.md_file):
        print(f"❌ 文件不存在: {args.md_file}", file=sys.stderr)
        sys.exit(1)

    rows = extract(args.md_file)
    if not rows:
        sys.exit(2)

    out = args.output
    if not out:
        base = os.path.splitext(args.md_file)[0]
        out = base + "_action_items.csv"

    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)

    print(f"✅ 已提取 {len(rows)} 条待办事项 -> {out}")


if __name__ == "__main__":
    main()
