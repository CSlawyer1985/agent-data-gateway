"""聊天记录导出解析（stdlib）。

支持：
- WhatsApp 导出 txt（多语言时间格式两种主流变体）
- Telegram 导出 JSON（messages 数组）
- 微信导出 txt（“昵称 时间”头行 + 内容行的常见工具格式）
- 通用 CSV 聊天导出（time/sender/content 模糊列名）

加密数据库（微信本地 db 等）不在本模块处理——scan 标记 unsupported 并给出指引。
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

PARSER_VERSION = "0.1.0"

# WhatsApp: [20/01/2024, 14:32:11] 张三: 消息  /  2024/1/20, 14:32 - 张三: 消息
_RE_WA_BRACKET = re.compile(
    r"^\[?(\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4})[, ]+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\]?\s*[-–]\s*([^:：]{1,40})[:：]\s?(.*)$"
)
_RE_WA_PLAIN = re.compile(
    r"^(\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4})[, ]+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\s*[-–]\s*([^:：]{1,40})[:：]\s?(.*)$"
)
# WhatsApp iOS 变体：[2024/01/20, 14:32:11] 张三: 消息（无破折号）
_RE_WA_NODASH = re.compile(
    r"^\[(\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\]\s+([^:：]{1,40})[:：]\s?(.*)$"
)
# 微信 txt：昵称 2024-01-20 14:32:11（头行独立成行，内容随后）
_RE_WX_HEADER = re.compile(r"^(.{1,32}?)\s{1,4}(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)\s*$")

_CSV_TIME_COLS = {"time", "date", "timestamp", "时间", "日期"}
_CSV_SENDER_COLS = {"sender", "from", "name", "nickname", "昵称", "发送人", "发送者", "说话人"}
_CSV_CONTENT_COLS = {"content", "message", "text", "msg", "内容", "消息"}


def detect_chat_format(sample: str) -> str | None:
    """给 scan 与 parse 共用的格式识别。sample 为前若干 KB 文本。"""
    if not sample.strip():
        return None
    stripped = sample.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(sample)
            if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
                return "telegram_json"
        except (json.JSONDecodeError, ValueError):
            # 大文件截断样本无法整解析，用特征串探测
            if '"messages"' in sample[:4096] and '"from"' in sample[:65536]:
                return "telegram_json"
    lines = [ln for ln in sample.split("\n") if ln.strip()][:40]
    if not lines:
        return None
    wa = sum(1 for ln in lines if _RE_WA_BRACKET.match(ln) or _RE_WA_PLAIN.match(ln) or _RE_WA_NODASH.match(ln))
    if wa >= max(2, int(len(lines) * 0.5)):
        return "whatsapp"
    wx = sum(1 for ln in lines if _RE_WX_HEADER.match(ln))
    if wx >= 2:
        return "wechat_txt"
    if lines[0].lower().startswith(("time", "date", "时间", "日期")) and ("," in lines[0] or "\t" in lines[0]):
        return "chat_csv"
    return None


def _parse_whatsapp(text: str, max_messages: int) -> tuple[list[dict], bool]:
    messages: list[dict] = []
    truncated = False
    cur: dict | None = None
    for line in text.split("\n"):
        m = _RE_WA_BRACKET.match(line) or _RE_WA_PLAIN.match(line) or _RE_WA_NODASH.match(line)
        if m:
            if cur:
                messages.append(cur)
            if len(messages) >= max_messages:
                truncated = True
                break
            date_s, time_s, sender, content = m.groups()
            cur = {"sender": sender.strip(), "timestamp": f"{date_s} {time_s}", "text": content}
        elif cur is not None and line.strip():
            cur["text"] += "\n" + line.rstrip()
    if cur is not None and len(messages) < max_messages:
        messages.append(cur)
    elif cur is not None:
        truncated = True
    return messages, truncated


def _parse_wechat_txt(text: str, max_messages: int) -> tuple[list[dict], bool]:
    messages: list[dict] = []
    truncated = False
    cur: dict | None = None
    for line in text.split("\n"):
        m = _RE_WX_HEADER.match(line.strip())
        if m:
            if cur:
                messages.append(cur)
            if len(messages) >= max_messages:
                truncated = True
                cur = None
                break
            cur = {"sender": m.group(1).strip(), "timestamp": m.group(2), "text": ""}
        elif cur is not None:
            cur["text"] = (cur["text"] + "\n" + line.rstrip()).strip("\n")
    if cur is not None and len(messages) < max_messages:
        messages.append(cur)
    return messages, truncated


def _parse_telegram_json(text: str, max_messages: int) -> tuple[list[dict], bool, dict]:
    obj = json.loads(text)
    meta = {"chat_name": obj.get("name"), "chat_type": obj.get("type"), "chat_id": obj.get("id")}
    messages: list[dict] = []
    truncated = False
    for m in obj.get("messages", []):
        if len(messages) >= max_messages:
            truncated = True
            break
        if m.get("type") != "message":
            continue
        txt = m.get("text", "")
        if isinstance(txt, list):
            parts = []
            for piece in txt:
                parts.append(piece.get("text", "") if isinstance(piece, dict) else str(piece))
            txt = "".join(parts)
        messages.append(
            {
                "id": m.get("id"),
                "sender": m.get("from") or m.get("actor") or "",
                "timestamp": m.get("date", ""),
                "text": txt,
            }
        )
    return messages, truncated, meta


def _parse_chat_csv(text: str, max_messages: int) -> tuple[list[dict], bool]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], False
    header = [h.strip().lower() for h in rows[0]]
    ti = next((i for i, h in enumerate(header) if h in _CSV_TIME_COLS), None)
    si = next((i for i, h in enumerate(header) if h in _CSV_SENDER_COLS), None)
    ci = next((i for i, h in enumerate(header) if h in _CSV_CONTENT_COLS), None)
    if ci is None:
        return [], False
    messages: list[dict] = []
    truncated = False
    for row in rows[1:]:
        if len(messages) >= max_messages:
            truncated = True
            break
        if len(row) <= ci:
            continue
        messages.append(
            {
                "sender": row[si] if si is not None and len(row) > si else "",
                "timestamp": row[ti] if ti is not None and len(row) > ti else "",
                "text": row[ci],
            }
        )
    return messages, truncated


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    from gateway.errors import WorkerFailed

    raw = source_path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise WorkerFailed("聊天导出编码无法识别", kind="unsupported")

    max_messages = limits.get("chat_max_messages", 2_000_000)
    fmt = format or detect_chat_format(text[:65536])
    notes: list[str] = []
    meta: dict = {}
    truncated = False
    if fmt in ("whatsapp", None):
        messages, truncated = _parse_whatsapp(text, max_messages)
        fmt = "whatsapp"
    elif fmt == "wechat_txt":
        messages, truncated = _parse_wechat_txt(text, max_messages)
    elif fmt == "telegram_json":
        try:
            messages, truncated, meta = _parse_telegram_json(text, max_messages)
        except (json.JSONDecodeError, ValueError) as e:
            raise WorkerFailed(f"Telegram JSON 解析失败: {e}", kind="crash")
    elif fmt == "chat_csv":
        messages, truncated = _parse_chat_csv(text, max_messages)
    else:
        raise WorkerFailed(f"未识别的聊天导出格式: {fmt}", kind="unsupported")

    if not messages:
        raise WorkerFailed("未识别出任何消息记录（格式可能不受支持）", kind="unsupported")

    # 按日期分组（message_group 块）
    groups: dict[str, list[dict]] = {}
    for i, m in enumerate(messages):
        day = (m.get("timestamp") or "")[:10] or "未知日期"
        m["seq"] = i
        groups.setdefault(day, []).append(m)

    blocks: list[dict] = []
    if meta:
        blocks.append(
            {
                "block_type": "section",
                "index": 0,
                "label": "会话信息",
                "elements": [
                    {"type": "field", "text": f"{k}: {v}", "locator": {"system": "field", "name": k}} for k, v in meta.items() if v is not None
                ],
            }
        )
    for gi, (day, msgs) in enumerate(sorted(groups.items())):
        elements = []
        for m in msgs:
            elements.append(
                {
                    "type": "message",
                    "text": m.get("text", ""),
                    "locator": {"system": "message", "id": m.get("id") if m.get("id") is not None else m["seq"], "timestamp": m.get("timestamp") or ""},
                    "attrs": {"sender": m.get("sender", "")},
                }
            )
        blocks.append({"block_type": "message_group", "index": gi + (1 if meta else 0), "label": day, "elements": elements})

    senders = sorted({m.get("sender", "") for m in messages if m.get("sender")})
    notes.append(f"格式: {fmt}；消息 {len(messages)} 条；参与者 {len(senders)} 人")
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass",
        "layout_preservation": "warning",
        "metadata": "pass" if meta else "warning",
        "source_anchoring": "pass",
    }
    return {
        "parser_name": "chat_lite",
        "parser_version": PARSER_VERSION,
        "config_hash": f"fmt={fmt}",
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "notes": notes,
        "truncated": truncated,
    }
