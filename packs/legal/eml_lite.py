"""EML 邮件解析（stdlib email）：头部元数据 + 正文 + 附件递归回灌。

附件写入 work_dir，fragment.attachments 声明，由编排器回灌为新 item（含嵌套 eml）。
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import re
from pathlib import Path

from .html_lite import _TextExtractor

PARSER_VERSION = "0.1.0"

_SAFE_NAME = re.compile(r"[^\w.\-一-鿿]+")


def _safe_filename(name: str, fallback: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip().strip(".")
    name = _SAFE_NAME.sub("_", name)
    if not name or name in (".", ".."):
        return fallback
    return name[:180]


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        return "".join(
            (b.decode(cs or "ascii", errors="replace") if isinstance(b, bytes) else b) for b, cs in parts
        )
    except (email.errors.HeaderParseError, LookupError, ValueError):
        return value


def _html_to_text(html_text: str) -> list[str]:
    ext = _TextExtractor()
    try:
        ext.feed(html_text)
    except Exception:
        pass
    return [t for _, t in ext.finish()]


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    notes: list[str] = []
    truncated = False
    max_att = limits.get("email_max_attachments", 500)
    max_att_bytes = limits.get("email_max_attachment_bytes", 512 * 1024 * 1024)
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        from gateway.errors import WorkerFailed

        raise WorkerFailed(f"eml 解析失败: {type(e).__name__}: {e}", kind="crash")

    # ---- 头部元数据 ----
    meta_fields: list[dict] = []
    for key, name in [
        ("From", "from"),
        ("To", "to"),
        ("Cc", "cc"),
        ("Subject", "subject"),
        ("Date", "date"),
        ("Message-ID", "message_id"),
        ("Return-Path", "return_path"),
        ("X-Originating-IP", "x_originating_ip"),
    ]:
        value = msg.get(key)
        if value:
            meta_fields.append(
                {
                    "type": "field",
                    "text": f"{name}: {_decode_header(str(value))}",
                    "locator": {"system": "field", "name": name},
                }
            )

    # ---- 正文与附件 ----
    body_elements: list[dict] = []
    attachment_entries: list[dict] = []  # fragment.attachments
    attachment_elements: list[dict] = []  # attachment_list block
    para_idx = 0
    att_count = 0
    html_fallback: list[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if filename:
            filename = _decode_header(filename)
        is_attachment = disp == "attachment" or (filename and ctype != "text/plain")
        if is_attachment or (filename and disp == "inline"):
            att_count += 1
            if att_count > max_att:
                truncated = True
                notes.append(f"附件数超过上限 {max_att}，其余未回灌")
                break
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception as e:
                notes.append(f"附件 {filename or att_count} 解码失败: {e}")
                continue
            if len(payload) > max_att_bytes:
                truncated = True
                notes.append(f"附件 {filename or att_count} 超过单附件上限，未回灌")
                continue
            safe = _safe_filename(filename or f"attachment-{att_count}.bin", f"attachment-{att_count}.bin")
            stored = f"att_{att_count}_{safe}"
            (work_dir / stored).write_bytes(payload)
            attachment_entries.append({"name": safe, "path": stored})
            attachment_elements.append(
                {
                    "type": "attachment_ref",
                    "text": safe,
                    "locator": {"system": "field", "name": f"attachment_{att_count}"},
                    "attrs": {"bytes": len(payload), "content_type": ctype, "original_filename": filename},
                }
            )
            continue
        # 正文部分
        if ctype == "text/plain" and disp != "attachment":
            try:
                text = part.get_content()
            except (LookupError, UnicodeDecodeError):
                payload = part.get_payload(decode=True) or b""
                text = payload.decode("utf-8", errors="replace")
                notes.append("正文编码回退为 utf-8 replace")
            for chunk in re.split(r"\n\s*\n", text):
                chunk = chunk.strip()
                if chunk:
                    body_elements.append(
                        {"type": "paragraph", "text": chunk, "locator": {"system": "paragraph", "index": para_idx}}
                    )
                    para_idx += 1
        elif ctype == "text/html" and disp != "attachment":
            try:
                html_text = part.get_content()
            except (LookupError, UnicodeDecodeError):
                payload = part.get_payload(decode=True) or b""
                html_text = payload.decode("utf-8", errors="replace")
            html_fallback = _html_to_text(html_text)

    if not body_elements and html_fallback:
        notes.append("无 text/plain 正文，已从 text/html 提取文本")
        for chunk in html_fallback:
            body_elements.append(
                {"type": "paragraph", "text": chunk, "locator": {"system": "paragraph", "index": para_idx}}
            )
            para_idx += 1

    blocks: list[dict] = [
        {"block_type": "section", "index": 0, "label": "邮件头", "elements": meta_fields},
        {"block_type": "section", "index": 1, "label": "正文", "elements": body_elements},
    ]
    if attachment_elements:
        blocks.append({"block_type": "attachment_list", "index": 2, "label": "附件清单", "elements": attachment_elements})

    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if body_elements else "warning",
        "layout_preservation": "warning",
        "attachments": "pass" if not truncated else "warning",
        "metadata": "pass" if meta_fields else "warning",
        "source_anchoring": "pass",
    }
    if not body_elements:
        notes.append("邮件无可提取正文（可能为纯附件邮件或正文加密）")
    return {
        "parser_name": "eml_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "notes": notes,
        "truncated": truncated,
        "attachments": attachment_entries,
    }
