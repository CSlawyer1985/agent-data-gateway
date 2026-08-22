#!/usr/bin/env python3
"""officecli worker adapter — ADG Worker Protocol v0.1。

officecli（brew install officecli）是规范实现的 OOXML CLI。本适配器用 `--json` 拿结构化结果：

  query <file> paragraph  → 正文段落（path /body/p[N] → paragraph 锚点、style → 标题层级）
  query <file> comment    → 批注（author/date/done/anchoredTo，anchoredTo 可挂回段落）
  query <file> revision   → 修订（revision.type=ins/del、author、date、nativePath）

诚实纪律：任一查询失败或输出无法解析时**必须失败**，绝不退化成"批注 0 条"——
"没有批注"与"我没读到批注"对律师是两回事。
"""

import json
import re
import subprocess
import sys

WORKER = {"name": "officecli", "version": "0.2.0", "maturity": "implemented", "license": "Apache-2.0"}

_P_INDEX = re.compile(r"/body/p\[(\d+)\]")


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class QueryFailed(Exception):
    pass


def _query(src: str, selector: str, timeout: int = 300) -> list[dict]:
    """执行一次 query 并返回 results；任何异常形态一律抛出，由调用方显性失败。"""
    proc = subprocess.run(
        ["officecli", "query", src, selector, "--json"],
        capture_output=True, text=True, errors="replace", timeout=timeout,
    )
    if proc.returncode != 0:
        raise QueryFailed(f"query {selector} 退出码 {proc.returncode}: {(proc.stderr or '')[-200:]}")
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        raise QueryFailed(f"query {selector} 输出不是合法 JSON: {e}") from e
    if not isinstance(data, dict) or not data.get("success"):
        raise QueryFailed(f"query {selector} 返回失败: {str(data)[:200]}")
    results = (data.get("data") or {}).get("results")
    if not isinstance(results, list):
        raise QueryFailed(f"query {selector} 缺少 results 数组")
    return results


def _paragraph_index(path: str | None) -> int | None:
    if not path:
        return None
    m = _P_INDEX.search(path)
    return int(m.group(1)) - 1 if m else None  # officecli 从 1 起，ADG locator 从 0 起


def _element_type(style: str | None) -> str:
    if not style:
        return "paragraph"
    m = re.match(r"Heading\s*([1-9])", style, re.I)
    if m:
        return f"heading{min(int(m.group(1)), 3)}"
    return "paragraph"


def handle_parse(req):
    src = req["source_path"]

    paragraphs = _query(src, "paragraph")
    elements = []
    for i, p in enumerate(paragraphs):
        text = (p.get("text") or "").strip()
        if not text:
            continue
        idx = _paragraph_index(p.get("path"))
        el = {
            "type": _element_type(p.get("style")),
            "text": text,
            "locator": {"system": "paragraph", "index": idx if idx is not None else i},
        }
        style = p.get("style")
        if style and style != "Normal":
            el["attrs"] = {"style": style}
        elements.append(el)

    comments = []
    for i, c in enumerate(_query(src, "comment")):
        fmt = c.get("format") or {}
        entry = {
            "id": str(fmt.get("id", i)),
            "author": fmt.get("author"),
            "date": fmt.get("date"),
            "text": (c.get("text") or "").strip(),
            "resolved": str(fmt.get("done", "")).lower() == "true",
            "thread": None,
        }
        anchor_idx = _paragraph_index(fmt.get("anchoredTo"))
        if anchor_idx is not None:
            entry["_anchor_locator"] = {"system": "paragraph", "index": anchor_idx}
        comments.append(entry)

    revisions = []
    for i, r in enumerate(_query(src, "revision")):
        fmt = r.get("format") or {}
        rtype = str(fmt.get("revision.type", "")).lower()
        entry = {
            "id": str(i),
            "kind": {"ins": "insert", "del": "delete"}.get(rtype, "other"),
            "author": fmt.get("revision.author"),
            "date": fmt.get("revision.date"),
            "text": (r.get("text") or "").strip(),
        }
        anchor_idx = _paragraph_index(fmt.get("revision.nativePath"))
        if anchor_idx is not None:
            entry["_anchor_locator"] = {"system": "paragraph", "index": anchor_idx}
        revisions.append(entry)

    notes = [
        f"officecli 多轨提取：正文 {len(elements)} 段、批注 {len(comments)} 条、修订 {len(revisions)} 条",
        "批注与修订已按 officecli 给出的锚点挂回段落",
    ]
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "officecli",
            "parser_version": WORKER["version"],
            "operation": "parse",
            "coordinate_system": {"name": "paragraph", "units": "index", "origin": "document-order"},
            "content": {"blocks": [{"block_type": "section", "index": 0, "label": "正文", "elements": elements}]},
            "annotations": {"comments": comments, "revisions": revisions},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if elements else "warning",
                "layout_preservation": "warning",
                "attachments": "pass",
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "notes": notes,
            "truncated": False,
        },
        "metrics": {"paragraphs": len(elements), "comments": len(comments), "revisions": len(revisions)},
    }


def main():
    line = sys.stdin.readline()
    try:
        req = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        send({"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "协议首行不是合法 JSON"}})
        return
    if req.get("type") == "capabilities_request":
        send({"type": "capabilities", "protocol_version": "0.1", "worker": WORKER,
              "handles": [{"kind": "docx", "role": "parse"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "officecli 不在 PATH（brew install officecli）"}}
        except QueryFailed as e:
            # 关键：不退化成"0 条"。读不到批注/修订必须显性失败，交给降级链换 parser。
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "error", "message": f"officecli 查询失败，拒绝给出可能缺轨的产物: {e}"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed",
          "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
