#!/usr/bin/env python3
"""
AI 翻译引擎
支持翻译：
  - translation-template/desktop-shell/zh-CN.json
  - translation-template/statsig/zh-CN.json
  - translation-template/ion-dist/parts/zh-CN.part-NNN.json

只翻译 template 中有、但 translated-zh-CN 对应文件中没有的条目。
翻译结果直接写回 translated-zh-CN/ 对应文件（增量合并，不覆盖已有翻译）。

用法：
  python translate_ai.py desktop-shell
  python translate_ai.py statsig
  python translate_ai.py ion-dist 1          # 翻译 part-001
  python translate_ai.py ion-dist 1 5        # 翻译 part-001 到 part-005
  python translate_ai.py ion-dist all        # 翻译全部分片

  python translate_ai.py check desktop-shell # 检查译文质量
  python translate_ai.py check statsig
  python translate_ai.py check ion-dist
  python translate_ai.py check all           # 检查全部

  --force      强制重新翻译（覆盖已有译文）
  --workers N  并发线程数（默认 5）
  --fix        （仅 check）从译文中删除回退/纯英文条目，便于下次重译
"""

import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# ── 路径 ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "translation-template"
TRANSLATED_DIR = ROOT / "translated-zh-CN"
PARTS_DIR = TEMPLATE_DIR / "ion-dist" / "parts"

# ── 配置 ──────────────────────────────────────────────────────────────────────
load_dotenv(ROOT / ".env", override=True)

BATCH_SIZE = 250
DEFAULT_WORKERS = 10

# ── 系统提示词 ────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "你是一名专业的软件本地化翻译员，负责将 Claude Desktop（Anthropic 出品的 AI 桌面客户端）"
    "的界面文案从英文翻译成简体中文。\n\n"
    "【产品背景】\n"
    "- 产品名称：Claude Desktop / Claude Cowork / Claude Code\n"
    "- 目标用户：中国大陆的开发者、AI 从业者、普通用户\n"
    "- 前端框架：Vue.js，i18n 使用 ICU MessageFormat\n\n"
    "【翻译规则】\n\n"
    "1. 占位符与标记——原样保留，不得修改：\n"
    "   - 花括号变量：{name}、{count}、{error}、{url} 等\n"
    "   - ICU 复数/选择语法：{count, plural, one {…} other {…}}、{gender, select, …}\n"
    "     其中 plural/select 关键字、one/other/male/female 等分支标签保持英文，\n"
    "     只翻译分支内的文案文字部分\n"
    "   - HTML/JSX 标签：<b>、<link>、<code>、<a>、<privacyLink> 等\n"
    "   - 转义字符：\\n、\\t 等\n"
    "   - 纯数字、单位、代码片段（如 {mA}mA、{kb}KB、{pct}%）\n\n"
    "2. 专有名词——保留英文，不翻译：\n"
    "   Claude、Claude Code、Claude Cowork、Anthropic、macOS、Windows、\n"
    "   BLE、API、JSON、UTF-8、OAuth、DXT、Statsig、Google Play、\n"
    "   Nordic UART Service 等品牌名、协议名、技术术语\n\n"
    "3. 翻译风格：\n"
    "   - 自然、口语化，符合中文产品习惯，避免机翻腔\n"
    "   - 界面按钮/标签用简短动词或名词（\"创建\"而非\"点击创建\"）\n"
    "   - 提示/说明句保持完整，语气友好\n"
    "   - 使用'你'而非'您'（产品风格偏年轻化）\n"
    "   - 标点使用中文全角（。？！，），但保留原文中的英文括号内内容不变\n\n"
    "4. 输入/输出格式：\n"
    "   - 输入：JSON 对象，key 为不透明 ID，value 为英文原文\n"
    "   - 输出：JSON 对象，key 与输入完全一致，value 为对应中文译文\n"
    "   - 只输出 JSON，不加任何解释、注释或 markdown 代码块\n"
)

# 打印锁，避免多线程输出交错
_print_lock = threading.Lock()


def tprint(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def get_client() -> tuple[OpenAI, str]:
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    if not base_url or not api_key:
        print("错误：请在 .env 中设置 OPENAI_BASE_URL 和 OPENAI_API_KEY")
        sys.exit(1)
    client = OpenAI(base_url=base_url, api_key=api_key)
    return client, model


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def translate_batch(client: OpenAI, model: str, batch: dict, label: str) -> dict:
    user_payload = json.dumps(batch, ensure_ascii=False)
    for attempt in range(3):
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            result = json.loads(content)
            # 只保留输入中存在的 key，过滤模型多余输出
            return {k: result[k] for k in batch if k in result and isinstance(result[k], str)}
        except Exception as e:
            elapsed = time.time() - t0
            if attempt == 2:
                tprint(f"  [{label}] [警告] 翻译失败，使用原文回退: {e}")
                return batch
            wait = 2 ** attempt
            tprint(f"  [{label}] [重试 {attempt+1}/3] 失败（{elapsed:.1f}s），{wait}s 后重试: {e}")
            time.sleep(wait)
    return batch


def translate_concurrent(
    client: OpenAI,
    model: str,
    pending: dict,
    output_path: Path,
    file_lock: threading.Lock,
    label: str,
    workers: int,
) -> None:
    keys = list(pending.keys())
    batches = [
        {k: pending[k] for k in keys[i: i + BATCH_SIZE]}
        for i in range(0, len(keys), BATCH_SIZE)
    ]
    total = len(keys)
    completed_count = 0

    tprint(f"[{label}] 待翻译 {total} 条，分 {len(batches)} 批，并发 {workers} 线程...")

    t_start = time.time()

    def do_batch(idx: int, batch: dict) -> dict:
        return translate_batch(client, model, batch, label=f"{label} 批{idx+1}/{len(batches)}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(do_batch, i, b): i for i, b in enumerate(batches)}
        for future in as_completed(futures):
            result = future.result()
            with file_lock:
                existing = load_json(output_path)
                existing.update(result)
                save_json(output_path, existing)
            completed_count += len(result)
            elapsed = time.time() - t_start
            tprint(f"  [{label}] 进度: {completed_count}/{total}，已用 {elapsed:.1f}s")

    total_elapsed = time.time() - t_start
    tprint(f"[{label}] 完成，总耗时 {total_elapsed:.1f}s → {output_path}")


def run_flat(client: OpenAI, model: str, name: str, force: bool, workers: int) -> None:
    template_path = TEMPLATE_DIR / name / "zh-CN.json"
    output_path = TRANSLATED_DIR / name / "zh-CN.json"
    template = load_json(template_path)
    existing = load_json(output_path)
    pending = dict(template) if force else {k: v for k, v in template.items() if k not in existing}

    if not pending:
        print(f"[{name}] 无新增条目，跳过。")
        return

    file_lock = threading.Lock()
    translate_concurrent(client, model, pending, output_path, file_lock, name, workers)


def run_parts(
    client: OpenAI,
    model: str,
    part_range: list[int] | None,
    force: bool,
    workers: int,
) -> None:
    part_files = sorted(PARTS_DIR.glob("zh-CN.part-*.json"))
    if not part_files:
        print("未找到分片文件，请先运行 split_template.py")
        return

    if part_range is None:
        targets = part_files
    else:
        start, end = part_range
        targets = [
            p for p in part_files
            if start <= int(re.search(r"part-(\d+)", p.name).group(1)) <= end
        ]

    if not targets:
        print("没有匹配的分片文件。")
        return

    output_path = TRANSLATED_DIR / "ion-dist" / "zh-CN.json"
    file_lock = threading.Lock()

    for part_path in targets:
        part_num = re.search(r"part-(\d+)", part_path.name).group(1)
        template = load_json(part_path)

        with file_lock:
            existing = load_json(output_path)

        pending = dict(template) if force else {k: v for k, v in template.items() if k not in existing}

        if not pending:
            print(f"[ion-dist/part-{part_num}] 无新增条目，跳过。")
            continue

        translate_concurrent(
            client, model, pending, output_path, file_lock,
            f"ion-dist/part-{part_num}", workers,
        )


def _has_chinese(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _has_letter(s: str) -> bool:
    return any(ch.isalpha() and ch.isascii() for ch in s)


def check_translation(name: str, fix: bool = False) -> tuple[int, int, int]:
    """检查 name 对应的译文质量，返回 (回退数, 缺失数, 纯英文数)。"""
    if name == "ion-dist":
        # 模板由所有分片合并而来
        part_files = sorted(PARTS_DIR.glob("zh-CN.part-*.json"))
        template: dict = {}
        for p in part_files:
            template.update(load_json(p))
        output_path = TRANSLATED_DIR / "ion-dist" / "zh-CN.json"
    else:
        template = load_json(TEMPLATE_DIR / name / "zh-CN.json")
        output_path = TRANSLATED_DIR / name / "zh-CN.json"

    translated = load_json(output_path)

    fallback: list[tuple[str, str]] = []     # 译文 == 原文
    missing: list[tuple[str, str]] = []      # 译文中没有
    english_only: list[tuple[str, str]] = [] # 有英文字母但无中文

    for k, src in template.items():
        if not isinstance(src, str):
            continue
        if k not in translated:
            missing.append((k, src))
            continue
        tgt = translated[k]
        if not isinstance(tgt, str):
            continue
        # 原文本身不含字母（纯符号/数字/占位符），无需检查
        if not _has_letter(src):
            continue
        if tgt == src:
            fallback.append((k, src))
        elif _has_letter(tgt) and not _has_chinese(tgt):
            english_only.append((k, tgt))

    total = len(template)
    print(f"\n[{name}] 模板 {total} 条，译文 {len(translated)} 条")
    print(f"  回退条目  (译文==原文)        : {len(fallback)}")
    print(f"  缺失条目  (译文中没有)        : {len(missing)}")
    print(f"  纯英文条目(含字母但无中文)    : {len(english_only)}")

    def _preview(items: list[tuple[str, str]], title: str, limit: int = 5) -> None:
        if not items:
            return
        print(f"  -- {title} 示例（前 {min(limit, len(items))} 条） --")
        for k, v in items[:limit]:
            v_short = v if len(v) <= 80 else v[:77] + "..."
            print(f"    {k}: {v_short}")

    _preview(fallback, "回退")
    _preview(missing, "缺失")
    _preview(english_only, "纯英文")

    if fix and (fallback or english_only):
        bad_keys = {k for k, _ in fallback} | {k for k, _ in english_only}
        cleaned = {k: v for k, v in translated.items() if k not in bad_keys}
        save_json(output_path, cleaned)
        print(f"  [--fix] 已从 {output_path.name} 删除 {len(bad_keys)} 条问题译文，下次运行翻译时将重新生成。")

    return len(fallback), len(missing), len(english_only)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    force = "--force" in args
    args = [a for a in args if a != "--force"]

    fix = "--fix" in args
    args = [a for a in args if a != "--fix"]

    workers = DEFAULT_WORKERS
    if "--workers" in args:
        idx = args.index("--workers")
        if idx + 1 >= len(args):
            print("--workers 需要指定数量，例如 --workers 10")
            sys.exit(1)
        workers = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    target = args[0].lower()

    # check 子命令：不需要 API 客户端
    if target == "check":
        if len(args) < 2:
            print("用法: python translate_ai.py check <desktop-shell|statsig|ion-dist|all> [--fix]")
            sys.exit(1)
        sub = args[1].lower()
        targets = ["desktop-shell", "statsig", "ion-dist"] if sub == "all" else [sub]
        totals = [0, 0, 0]
        for t in targets:
            if t not in ("desktop-shell", "statsig", "ion-dist"):
                print(f"未知检查目标: {t}")
                sys.exit(1)
            a, b, c = check_translation(t, fix=fix)
            totals[0] += a; totals[1] += b; totals[2] += c
        if len(targets) > 1:
            print(f"\n[汇总] 回退 {totals[0]}，缺失 {totals[1]}，纯英文 {totals[2]}")
        return

    client, model = get_client()

    if target in ("desktop-shell", "statsig"):
        run_flat(client, model, target, force=force, workers=workers)

    elif target == "ion-dist":
        if len(args) < 2:
            print("用法: python translate_ai.py ion-dist <part编号|all> [结束编号] [--force] [--workers N]")
            sys.exit(1)

        if args[1].lower() == "all":
            run_parts(client, model, None, force=force, workers=workers)
        else:
            start = int(args[1])
            end = int(args[2]) if len(args) >= 3 else start
            run_parts(client, model, [start, end], force=force, workers=workers)

    else:
        print(f"未知目标: {target}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()