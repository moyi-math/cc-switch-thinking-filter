# -*- coding: utf-8 -*-
"""
clean_empty_thinking.py — 修复 CC Switch 代理写入 Codex 会话历史中的"空 thinking"毒记录。

背景: CC Switch 本地代理会把上游返回的空 thinking 块以
  ccswitch-anthropic-thinking-v1:<base64> 格式写进 ~/.codex/sessions/**/*.jsonl,
  下次请求原样回传时, 上游网关(如基元律动)校验 thinking 长度不足 -> HTTP 400
  (messages.N.content.0.thinking 长度不足)。

本脚本扫描会话文件, 删除/修复这类 reasoning 记录。
用法:
  python clean_empty_thinking.py <文件或目录> [--min-len 4] [--dry-run] [--keep-backup]
"""
import argparse
import base64
import datetime
import json
import os
import shutil
import sys

PREFIX = "ccswitch-anthropic-thinking-v1:"


def decode_thinking(encrypted_content: str):
    """解码 ccswitch thinking; 返回 (ok, thinking_text 或错误说明)。"""
    if not encrypted_content or not encrypted_content.startswith(PREFIX):
        return False, None  # 非 ccswitch 格式(Codex 原生加密), 跳过
    b64 = encrypted_content[len(PREFIX):]
    try:
        b64 += "=" * (-len(b64) % 4)
        raw = base64.b64decode(b64).decode("utf-8", "replace")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return False, None
        return True, obj.get("thinking", "")
    except Exception as e:
        return True, f"<decode-error: {e}>"  # 无法解析, 保守跳过


def is_poisoned(item: dict, min_len: int):
    if item.get("type") != "reasoning":
        return False
    enc = item.get("encrypted_content") or ""
    if not enc.startswith(PREFIX):
        return False
    ok, thinking = decode_thinking(enc)
    if not ok:
        return False
    if isinstance(thinking, str) and len(thinking.strip()) < min_len:
        return True
    return False


def process_file(path: str, min_len: int, dry_run: bool, keep_backup: bool):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    removed = []
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except Exception:
            out.append(ln)  # 不可解析的行保留
            continue
        if rec.get("type") == "response_item":
            payload = rec.get("payload", {})
            item = payload.get("item") or payload
            if is_poisoned(item, min_len):
                rid = item.get("id", "?")
                enc = item.get("encrypted_content", "")
                ok, thinking = decode_thinking(enc)
                removed.append((rec.get("timestamp", ""), rid, repr(thinking)[:60] if isinstance(thinking, str) else str(thinking)))
                continue  # 删除该行
        out.append(ln)

    if removed and not dry_run:
        if keep_backup:
            bak = f"{path}.bak-{datetime.datetime.now():%Y%m%d_%H%M%S}"
            shutil.copy2(path, bak)
            print(f"  备份: {bak}")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(out)
    return removed, len(lines), len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="会话 jsonl 文件或目录")
    ap.add_argument("--min-len", type=int, default=4, help="thinking 最小长度阈值(默认 4, 可调)")
    ap.add_argument("--dry-run", action="store_true", help="只扫描不修改")
    ap.add_argument("--no-backup", action="store_true", help="不创建备份(默认自动备份)")
    args = ap.parse_args()

    if os.path.isdir(args.target):
        files = []
        for root, _, names in os.walk(args.target):
            for n in names:
                if n.endswith(".jsonl"):
                    files.append(os.path.join(root, n))
    else:
        files = [args.target]

    total_removed = 0
    for fp in sorted(files):
        removed, before, after = process_file(fp, args.min_len, args.dry_run, not args.no_backup)
        if removed:
            print(f"[{fp}]")
            print(f"  行数 {before} -> {after}, 移除 {len(removed)} 条空 thinking reasoning:")
            for ts, rid, th in removed:
                print(f"    - {ts}  {rid}  thinking={th}")
            total_removed += len(removed)
        else:
            print(f"[{fp}] 干净 (无空 thinking)")

    print(f"\n合计移除: {total_removed} 条" + ("  [DRY-RUN 未写入]" if args.dry_run else ""))
    sys.exit(1 if total_removed else 0)


if __name__ == "__main__":
    main()
