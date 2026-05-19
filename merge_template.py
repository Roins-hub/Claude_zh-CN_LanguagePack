#!/usr/bin/env python3
"""
合并 translation-template/ion-dist/parts/ 下的所有分片
按文件名顺序拼接，输出到 translated-zh-CN/ion-dist/zh-CN.json
"""
import json
import os
import glob

PARTS_DIR = os.path.join(os.path.dirname(__file__),
                         "translation-template", "ion-dist", "parts")
OUT = os.path.join(os.path.dirname(__file__),
                   "translated-zh-CN", "ion-dist", "zh-CN.json")

part_files = sorted(glob.glob(os.path.join(PARTS_DIR, "zh-CN.part-*.json")))
if not part_files:
    print(f"未找到分片文件，请检查目录: {PARTS_DIR}")
    raise SystemExit(1)

merged = {}
for path in part_files:
    with open(path, encoding="utf-8") as f:
        chunk = json.load(f)
    merged.update(chunk)
    print(f"  {os.path.basename(path)}: {len(chunk)} 条")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)

print(f"\n共合并 {len(merged)} 条 → {OUT}")
