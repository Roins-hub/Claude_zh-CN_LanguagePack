#!/usr/bin/env python3
"""
拆分 translation-template/ion-dist/zh-CN.json
每 500 条属性生成一个分片文件 zh-CN.part-NNN.json
"""
import json
import os

CHUNK_SIZE = 2500
SRC = os.path.join(os.path.dirname(__file__),
                   "translation-template", "ion-dist", "zh-CN.json")
OUT_DIR = os.path.join(os.path.dirname(__file__),
                       "translation-template", "ion-dist", "parts")

os.makedirs(OUT_DIR, exist_ok=True)

with open(SRC, encoding="utf-8") as f:
    data = json.load(f)

keys = list(data.keys())
total = len(keys)
chunks = [keys[i:i + CHUNK_SIZE] for i in range(0, total, CHUNK_SIZE)]

for idx, chunk_keys in enumerate(chunks, start=1):
    chunk = {k: data[k] for k in chunk_keys}
    out_path = os.path.join(OUT_DIR, f"zh-CN.part-{idx:03d}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(chunk, f, ensure_ascii=False, indent=2)
    print(f"  part-{idx:03d}: {len(chunk)} 条 → {out_path}")

print(f"\n共 {total} 条，拆为 {len(chunks)} 个分片，输出目录: {OUT_DIR}")
