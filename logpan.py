#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logpan - 轻量日志扫描工具（DKail 配套）
只依赖 Python 标准库。扫描日志文件，按关键字或正则匹配，
输出命中计数、首末位置、按文件/模式汇总，支持导出 JSON。

用法:
    python logpan.py -f app.log -k ERROR -k timeout
    python logpan.py -f a.log b.log -r "ERROR|FATAL" -c 20
    python logpan.py -f app.log -k ERROR --json -o report.json
    cat app.log | python logpan.py -k Traceback -
"""

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime

VERSION = "0.2.0"
MAX_SNIPPET = 240          # 单条片段最大长度
DEFAULT_CONTEXT = 0        # 默认前后文行数
TAIL_DEFAULT = 50          # 未命中时的 tail 行数（0 关闭）
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)


def build_patterns(args):
    """把 -k 关键字和 -r 正则编译成 [(label, compiled, is_regex), ...]。"""
    patterns = []
    for kw in args.keyword or []:
        # 关键字默认大小写不敏感；关键字本身按正则转义，避免误伤
        patterns.append((kw, re.compile(re.escape(kw), re.IGNORECASE), False))
    for rx in args.regex or []:
        try:
            patterns.append((rx, re.compile(rx), True))
        except re.error as e:
            die("非法正则 %r: %s" % (rx, e))
    return patterns


def iter_files(args):
    """展开文件参数：目录递归(受 --glob 限制)、通配符、'-' 表示 stdin。"""
    seen = set()
    for item in args.file:
        if item == "-":
            yield "-"
            continue
        path = item
        if os.path.isdir(path):
            for root, dirs, names in os.walk(path):
                dirs.sort()
                for name in sorted(names):
                    if args.glob and not fnmatch.fnmatch(name, args.glob):
                        continue
                    p = os.path.join(root, name)
                    if p not in seen and not os.path.islink(p):
                        seen.add(p)
                        yield p
        elif os.path.isfile(path):
            if path not in seen:
                seen.add(path)
                yield path
        elif any(ch in path for ch in "*?["):
            matches = sorted(g for g in
                             __import__("glob").glob(path)
                             if os.path.isfile(g))
            for p in matches:
                if p not in seen:
                    seen.add(p)
                    yield p
        else:
            warn("跳过不存在的路径: %s" % path)


def first_ts(line):
    m = TIMESTAMP_RE.search(line)
    return m.group(0) if m else None


def scan_stream(fh, label, patterns, ctx):
    """扫描一个已打开的文本流，返回 per-pattern 统计与命中行列表。"""
    stats = OrderedDict(
        (label_p, {"count": 0, "first": None, "last": None,
                   "first_line": None, "last_line": None,
                   "first_ts": None, "last_ts": None})
        for label_p, _p, _r in patterns
    )
    hits = []
    prev_buf = []
    for i, line in enumerate(fh, 1):
        line = line.rstrip("\r\n")
        ts = None
        for plabel, pat, is_rx in patterns:
            m = pat.search(line)
            if not m:
                continue
            s = stats[plabel]
            s["count"] += 1
            if s["first"] is None:
                s["first"] = i
                s["first_line"] = line
                ts = ts or first_ts(line)
                s["first_ts"] = ts
            s["last"] = i
            s["last_line"] = line
            ts = ts or first_ts(line)
            s["last_ts"] = ts
            snippet = line if len(line) <= MAX_SNIPPET \
                else line[:MAX_SNIPPET - 3] + "..."
            hit = {"file": label, "line": i, "pattern": plabel,
                   "snippet": snippet}
            if is_rx and m.groups():
                hit["groups"] = [g for g in m.groups() if g is not None]
            hits.append(hit)
            if ctx:
                hit["before"] = list(prev_buf)[-ctx:]
        if ctx:
            prev_buf.append(line)
            if len(prev_buf) > ctx:
                prev_buf.pop(0)
    return stats, hits


def compile_pattern(pattern, regex=False):
    """把单个模式编译成 (label, compiled, is_regex) 三元组。

    字面模式转义后当正则用——语义就是大小写敏感的子串匹配；
    regex=True 时按正则 search。空模式直接 ValueError（调用方传空串
    基本就是 bug，早炸早好）。非法正则原样抛 re.error，不吞。
    """
    if pattern is None or str(pattern).strip() == "":
        raise ValueError("pattern 不能为空")
    text = str(pattern)
    is_rx = bool(regex)
    compiled = re.compile(text if is_rx else re.escape(text))
    return (text, compiled, is_rx)


def scan_lines(lines, pattern, regex=False):
    """单元级扫描：对一组行匹配，返回命中行的原文列表。

    这是给测试与复用方的薄封装（CLI 主路径走 scan_stream，那边要统计
    首末位置和上下文）。语义：字面子串（大小写敏感）/ 正则 search。
    """
    _label, compiled, _is_rx = compile_pattern(pattern, regex)
    return [ln for ln in lines if compiled.search(ln)]


def scan_file(path, pattern, regex=False):
    """单元级文件扫描：读文件后走 scan_lines。

    "-" 表示 stdin；文件不存在抛 FileNotFoundError（调用方自己决定
    是跳过还是报错——工具不该替用户决定）。
    """
    if path == "-":
        return scan_lines((ln.rstrip("\r\n") for ln in sys.stdin),
                          pattern, regex)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    return scan_lines([ln.rstrip("\r\n") for ln in read_lines(path)],
                      pattern, regex)


def read_lines(path):
    """容错读取：- 为 stdin；二进制/未知编码逐行降级。"""
    if path == "-":
        for raw in sys.stdin.buffer:
            yield decode(raw)
        return
    with open(path, "rb") as fh:
        for raw in fh:
            yield decode(raw)


def decode(raw):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def tail_lines(path, n):
    if n <= 0:
        return
    src = sys.stdin.buffer if path == "-" else open(path, "rb")
    try:
        lines = src.read().splitlines()
    finally:
        if path != "-":
            src.close()
    if lines:
        out("  -- tail %d --" % n)
        for raw in lines[-n:]:
            out("  " + decode(raw).rstrip())


def print_report(results, args):
    """人读输出：先汇总表，再按需展开命中明细。"""
    total_files = len(results)
    grand = 0
    out("=" * 62)
    out("logpan %s  扫描于 %s" % (VERSION, datetime.now().isoformat(timespec="seconds")))
    out("文件数: %d  模式数: %d" % (total_files, len(build_patterns(args))))
    out("=" * 62)
    for res in results:
        out("\n[%s]  (%d 行)" % (res["file"], res["lines"]))
        for plabel, s in res["stats"].items():
            grand += s["count"]
            if s["count"]:
                ts = ""
                if s["first_ts"]:
                    ts = "  ts[%s .. %s]" % (s["first_ts"], s["last_ts"])
                out("  %-30r 命中 %6d  行 %s..%s%s"
                    % (plabel, s["count"], s["first"], s["last"], ts))
                if args.verbose:
                    out("    first: %s" % clip(s["first_line"]))
                    out("    last : %s" % clip(s["last_line"]))
            else:
                out("  %-30r 命中      0" % plabel)
        if not any(s["count"] for s in res["stats"].values()):
            tail_lines(res["file"] if res["file"] != "-" else "-", TAIL_DEFAULT)
    out("-" * 62)
    out("总命中: %d" % grand)
    if args.top and args.hits:
        out("\nTop 模式:")
        merged = {}
        for res in results:
            for pl, s in res["stats"].items():
                merged[pl] = merged.get(pl, 0) + s["count"]
        for pl, c in sorted(merged.items(), key=lambda x: -x[1])[:args.top]:
            if c:
                out("  %6d  %r" % (c, pl))


def clip(s, n=MAX_SNIPPET):
    return s if len(s) <= n else s[:n - 3] + "..."


def dump_json(results, hits, args, path):
    doc = {
        "tool": "logpan", "version": VERSION,
        "time": datetime.now().isoformat(timespec="seconds"),
        "patterns": {"keywords": args.keyword or [],
                     "regexes": args.regex or []},
        "files": [
            {"file": r["file"], "lines": r["lines"],
             "stats": r["stats"]} for r in results
        ],
        "hits": hits if args.hits else [],
    }
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        err("已写入 %s" % path)
    else:
        json.dump(doc, sys.stdout, ensure_ascii=False, indent=2)
        out("")


def out(msg):
    sys.stdout.write(msg + "\n")


def err(msg):
    sys.stderr.write("[logpan] %s\n" % msg)


def warn(msg):
    err("警告: " + msg)


def die(msg, code=2):
    err("错误: " + msg)
    sys.exit(code)


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="logpan",
        description="轻量日志扫描：关键字/正则匹配 + 命中汇总（DKail 配套）")
    ap.add_argument("-f", "--file", nargs="+", required=True,
                    help="日志文件/目录/通配符，'-' 表示 stdin")
    ap.add_argument("-k", "--keyword", action="append",
                    help="关键字（大小写不敏感，可多次）")
    ap.add_argument("-r", "--regex", action="append",
                    help="正则表达式（可多次）")
    ap.add_argument("-c", "--context", type=int, default=DEFAULT_CONTEXT,
                    help="命中行之前的上下文行数（默认 0）")
    ap.add_argument("--glob", help="扫描目录时的文件名过滤，如 '*.log'")
    ap.add_argument("-t", "--top", type=int, default=5,
                    help="汇总后展示 Top N 模式（默认 5）")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="输出每模式首/末命中行")
    ap.add_argument("--hits", action="store_true",
                    help="在 JSON 中保留全部命中明细")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出结果")
    ap.add_argument("-o", "--output", help="JSON 写入文件（配合 --json）")
    ap.add_argument("--tail", type=int, default=TAIL_DEFAULT,
                    help="无命中文件展示的 tail 行数（默认 50，0 关闭）")
    ap.add_argument("--version", action="version",
                    version="logpan %s" % VERSION)
    args = ap.parse_args(argv)
    if not args.keyword and not args.regex:
        ap.error("至少提供一个 -k 关键字或 -r 正则")
    args.tail_default = TAIL_DEFAULT
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    patterns = build_patterns(args)
    results, hits = [], []
    for path in iter_files(args):
        try:
            stats, file_hits = scan_stream(
                sys.stdin if path == "-" else open(path, "r", encoding="utf-8",
                                                   errors="replace"),
                path, patterns, args.context)
        except OSError as e:
            warn("无法读取 %s: %s" % (path, e))
            continue
        else:
            if path != "-":
                fh.close() if False else None
        nlines = max(
            (s["last"] or 0) for s in stats.values()) if stats else 0
        results.append({"file": path, "lines": nlines, "stats": stats})
        hits.extend(file_hits)
    if not results:
        die("没有可扫描的文件")
    if args.json:
        dump_json(results, hits, args, args.output)
    else:
        print_report(results, args)
    # 退出码：有命中 0，无命中 1
    sys.exit(0 if hits or args.json and not hits and
             any(s["count"] for r in results for s in r["stats"].values())
             else 1)


if __name__ == "__main__":
    main()