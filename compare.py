#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比较两个 LEF 文件的 Pin 差异。
用法:
    python compare_lef_pins.py old.lef new.lef
    python compare_lef_pins.py old.lef new.lef --macro ADDER
    python compare_lef_pins.py old.lef new.lef --full
"""
import re
import sys
import difflib
import argparse
from collections import OrderedDict


class Pin:
    """保存一个 Pin 的解析信息"""
    def __init__(self, name):
        self.name = name
        self.attrs = {}        # DIRECTION / USE / SHAPE 等单行属性
        self.layers = []       # PORT 内的 LAYER 名称
        self.raw_lines = []    # Pin 内部行的原始文本（不含 END）

    def add_layer(self, layer):
        if layer not in self.layers:
            self.layers.append(layer)


def parse_lef(filepath):
    """
    解析 LEF 文件，返回:
        OrderedDict[macro_name][pin_name] -> Pin
    """
    macros = OrderedDict()
    stack = []                # 上下文栈：macro / pin / port
    current_macro = None
    current_pin = None

    try:
        f = open(filepath, 'r', errors='ignore')
    except OSError as e:
        print(f"无法打开文件 {filepath}: {e}")
        sys.exit(1)

    with f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()

            # 1. 处理 END 行
            if re.match(r'^END\s*$', stripped, re.I) or re.match(r'^END\s+\S+', stripped, re.I):
                if stack:
                    ctx = stack.pop()
                    if ctx['type'] == 'pin':
                        current_pin = None
                    elif ctx['type'] == 'macro':
                        current_macro = None
                    # port 结束：不改变 current_pin / current_macro
                continue

            # 2. 如果当前在 Pin 内部，记录内容、提取属性
            if current_pin is not None:
                current_pin.raw_lines.append(stripped)

                # 常见单行属性：DIRECTION / USE / SHAPE 等
                attr_match = re.match(
                    r'^(DIRECTION|USE|SHAPE|LEAKAGE|ANTENNADIFFAREA|POWER|GROUND'
                    r'|CAPACITANCE|MAXCAP|MAXLOAD)\s+(.*?)\s*;?\s*$',
                    stripped, re.I)
                if attr_match:
                    key = attr_match.group(1).upper()
                    value = attr_match.group(2).strip().rstrip(';').strip()
                    current_pin.attrs[key] = value

                # PORT 内的 LAYER
                layer_match = re.match(r'^LAYER\s+(\S+)\s*;?$', stripped, re.I)
                if layer_match:
                    current_pin.add_layer(layer_match.group(1))

                # PORT 开始
                if re.match(r'^PORT\s*$', stripped, re.I):
                    stack.append({'type': 'port', 'name': None})

                continue

            # 3. 不在 Pin 内部时，识别 MACRO 和 PIN
            macro_match = re.match(r'^MACRO\s+(\S+)', stripped, re.I)
            if macro_match and current_pin is None:
                macro_name = macro_match.group(1)
                if macro_name not in macros:
                    macros[macro_name] = OrderedDict()
                current_macro = macro_name
                stack.append({'type': 'macro', 'name': macro_name})
                continue

            pin_match = re.match(r'^PIN\s+(\S+)', stripped, re.I)
            if pin_match and current_macro is not None and current_pin is None:
                pin_name = pin_match.group(1)
                pin = Pin(pin_name)
                macros[current_macro][pin_name] = pin
                current_pin = pin
                stack.append({'type': 'pin', 'name': pin_name})
                continue

    return macros


def compare_pin(pin1, pin2, full=False):
    """比较两个同名 Pin，返回差异字符串列表"""
    diffs = []

    # 比较单行属性
    all_keys = sorted(set(pin1.attrs) | set(pin2.attrs))
    for key in all_keys:
        v1 = pin1.attrs.get(key)
        v2 = pin2.attrs.get(key)
        if v1 != v2:
            old = v1 if v1 is not None else "<none>"
            new = v2 if v2 is not None else "<none>"
            diffs.append(f"{key}: {old} -> {new}")

    # 比较 Layer 列表
    if pin1.layers != pin2.layers:
        diff_layers = set(pin1.layers) ^ set(pin2.layers)
        diffs.append(f"LAYER 变化: {pin1.layers} -> {pin2.layers} (差异: {sorted(diff_layers)})")

    # 可选的完整文本 diff
    if full:
        text_diff = list(difflib.unified_diff(
            pin1.raw_lines,
            pin2.raw_lines,
            fromfile="old pin " + pin1.name,
            tofile="new pin " + pin2.name,
            lineterm=""
        ))
        if text_diff:
            diffs.append("完整文本差异:")
            diffs.extend("    " + line for line in text_diff)

    return diffs


def main():
    parser = argparse.ArgumentParser(description="比较两个 LEF 文件的 Pin 差异")
    parser.add_argument("old_lef", help="原始 LEF 文件路径")
    parser.add_argument("new_lef", help="更新后的 LEF 文件路径")
    parser.add_argument("--macro", help="只比较指定宏名称", default=None)
    parser.add_argument("--full", action="store_true",
                        help="显示 Pin 内部完整文本差异")
    args = parser.parse_args()

    old_macros = parse_lef(args.old_lef)
    new_macros = parse_lef(args.new_lef)

    all_macros = set(old_macros) | set(new_macros)
    if args.macro:
        if args.macro not in all_macros:
            print(f"指定的宏 {args.macro} 不存在于任一 LEF 中")
            sys.exit(1)
        all_macros = {args.macro}

    total_add = 0
    total_remove = 0
    total_diff = 0

    for macro in sorted(all_macros):
        print("=" * 60)
        print(f"MACRO: {macro}")

        old_pins = old_macros.get(macro, {})
        new_pins = new_macros.get(macro, {})

        if not old_pins and new_pins:
            print("  宏仅存在于新 LEF，所有 Pin 为新增")
            total_add += len(new_pins)
            for p in sorted(new_pins):
                print(f"    + {p}")
            continue
        if old_pins and not new_pins:
            print("  宏仅存在于旧 LEF，所有 Pin 被删除")
            total_remove += len(old_pins)
            for p in sorted(old_pins):
                print(f"    - {p}")
            continue

        old_names = set(old_pins)
        new_names = set(new_pins)

        removed = old_names - new_names
        added = new_names - old_names
        common = old_names & new_names

        for p in sorted(removed):
            print(f"  - 已删除 Pin: {p}")
            total_remove += 1

        for p in sorted(added):
            print(f"  + 新增 Pin: {p}")
            total_add += 1

        for p in sorted(common):
            diffs = compare_pin(old_pins[p], new_pins[p], full=args.full)
            if diffs:
                print(f"  ~ Pin 差异: {p}")
                for d in diffs:
                    print(f"      {d}")
                total_diff += 1

        if not removed and not added and total_diff == 0:
            print("  Pin 无差异")

    print("=" * 60)
    print(f"汇总: 新增 Pin {total_add} 个, 删除 Pin {total_remove} 个, 属性/内容变化 Pin {total_diff} 个")


if __name__ == "__main__":
    main()
