"""魔数类型识别（stdlib 实现，Tika worker 为可选增强）。

返回 DetectedType(mime, magic, method, confidence) + kind + format。
kind 受控词表：pdf/docx/doc/xlsx/xls/csv/image/text/html/email/chat/audio/video/
archive/dxf/dwg/presentation/database/executable/unknown。
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

# ---- kind 常量 ----
PDF = "pdf"
DOCX = "docx"
DOC = "doc"
XLSX = "xlsx"
XLS = "xls"
CSV = "csv"
IMAGE = "image"
TEXT = "text"
HTML = "html"
EMAIL = "email"
CHAT = "chat"
AUDIO = "audio"
VIDEO = "video"
ARCHIVE = "archive"
DXF = "dxf"
DWG = "dwg"
PRESENTATION = "presentation"
DATABASE = "database"
EXECUTABLE = "executable"
UNKNOWN = "unknown"


@dataclass
class DetectedType:
    mime: str
    magic: str
    method: str  # adg-magic / extension-fallback / container-derived / tika
    confidence: str  # high / medium / low
    kind: str
    format: str | None = None  # 如 image/jpeg、application/zip、message/rfc822

    def to_dict(self) -> dict:
        d = {"mime": self.mime, "magic": self.magic, "method": self.method}
        d["confidence"] = self.confidence
        return d


# 扩展名提示（仅作低置信兜底与二级消歧）
_EXT_HINTS: dict[str, tuple[str, str, str]] = {
    # ext: (kind, mime, format)
    ".pdf": (PDF, "application/pdf", None),
    ".docx": (DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None),
    ".doc": (DOC, "application/msword", None),
    ".odt": (DOC, "application/vnd.oasis.opendocument.text", "odt"),
    ".xlsx": (XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", None),
    ".xls": (XLS, "application/vnd.ms-excel", None),
    ".csv": (CSV, "text/csv", None),
    ".tsv": (CSV, "text/tab-separated-values", "tsv"),
    ".txt": (TEXT, "text/plain", None),
    ".md": (TEXT, "text/markdown", "markdown"),
    ".log": (TEXT, "text/plain", None),
    ".html": (HTML, "text/html", None),
    ".htm": (HTML, "text/html", None),
    ".mhtml": (HTML, "multipart/related", "mhtml"),
    ".eml": (EMAIL, "message/rfc822", "eml"),
    ".msg": (EMAIL, "application/vnd.ms-outlook", "msg"),
    ".pst": (EMAIL, "application/vnd.ms-outlook-pst", "pst"),
    ".ost": (EMAIL, "application/vnd.ms-outlook-pst", "ost"),
    ".jpg": (IMAGE, "image/jpeg", None),
    ".jpeg": (IMAGE, "image/jpeg", None),
    ".png": (IMAGE, "image/png", None),
    ".gif": (IMAGE, "image/gif", None),
    ".bmp": (IMAGE, "image/bmp", None),
    ".tiff": (IMAGE, "image/tiff", None),
    ".tif": (IMAGE, "image/tiff", None),
    ".webp": (IMAGE, "image/webp", None),
    ".heic": (IMAGE, "image/heic", None),
    ".zip": (ARCHIVE, "application/zip", "zip"),
    ".rar": (ARCHIVE, "application/vnd.rar", "rar"),
    ".7z": (ARCHIVE, "application/x-7z-compressed", "7z"),
    ".tar": (ARCHIVE, "application/x-tar", "tar"),
    ".gz": (ARCHIVE, "application/gzip", "gzip"),
    ".tgz": (ARCHIVE, "application/gzip", "tar+gzip"),
    ".mp3": (AUDIO, "audio/mpeg", None),
    ".wav": (AUDIO, "audio/wav", None),
    ".flac": (AUDIO, "audio/flac", None),
    ".m4a": (AUDIO, "audio/mp4", None),
    ".ogg": (AUDIO, "audio/ogg", None),
    ".mp4": (VIDEO, "video/mp4", None),
    ".mov": (VIDEO, "video/quicktime", None),
    ".avi": (VIDEO, "video/x-msvideo", None),
    ".mkv": (VIDEO, "video/x-matroska", None),
    ".dxf": (DXF, "image/vnd.dxf", None),
    ".dwg": (DWG, "image/vnd.dwg", None),
    ".rvt": (DATABASE, "application/x-revit", "rvt"),
    ".pptx": (PRESENTATION, "application/vnd.openxmlformats-officedocument.presentationml.presentation", None),
    ".ppt": (PRESENTATION, "application/vnd.ms-powerpoint", None),
    ".sqlite": (DATABASE, "application/vnd.sqlite3", None),
    ".db": (DATABASE, "application/vnd.sqlite3", None),
    ".json": (TEXT, "application/json", "json"),
    ".xml": (TEXT, "application/xml", "xml"),
}

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FTYP_BRANDS = {
    b"heic": (IMAGE, "image/heic"),
    b"heix": (IMAGE, "image/heic"),
    b"mif1": (IMAGE, "image/heif"),
    b"mp41": (VIDEO, "video/mp4"),
    b"mp42": (VIDEO, "video/mp4"),
    b"isom": (VIDEO, "video/mp4"),
    b"qt  ": (VIDEO, "video/quicktime"),
    b"M4A ": (AUDIO, "audio/mp4"),
    b"M4V ": (VIDEO, "video/mp4"),
}


def _detect_pk_container(path: Path, head: bytes) -> DetectedType:
    """PK 开头：区分 OOXML（docx/xlsx/pptx）与普通 zip。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist()[:200])
    except (zipfile.BadZipFile, OSError):
        return DetectedType("application/zip", "PK", "adg-magic", "low", ARCHIVE, "zip")
    prefix_names = {n.split("/")[0] + "/" for n in names if "/" in n}
    if "word/document.xml" in names:
        return DetectedType("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "PK+word/document.xml", "adg-magic", "high", DOCX)
    if "xl/workbook.xml" in names:
        return DetectedType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "PK+xl/workbook.xml", "adg-magic", "high", XLSX)
    if "ppt/presentation.xml" in names or "ppt/" in prefix_names:
        return DetectedType("application/vnd.openxmlformats-officedocument.presentationml.presentation", "PK+ppt/", "adg-magic", "high", PRESENTATION)
    if "mimetype" in names and any(n.startswith("word/") or n.startswith("content.xml") for n in names):
        return DetectedType("application/vnd.oasis.opendocument.text", "PK+odf", "adg-magic", "medium", DOC, "odt")
    return DetectedType("application/zip", "PK\\x03\\x04", "adg-magic", "high", ARCHIVE, "zip")


def _detect_ole2(ext: str) -> DetectedType:
    """OLE2 复合文档：stdlib 无法列流，按扩展名给中置信判断。"""
    if ext == ".msg":
        return DetectedType("application/vnd.ms-outlook", "OLE2+ext", "adg-magic", "medium", EMAIL, "msg")
    if ext == ".doc":
        return DetectedType("application/msword", "OLE2+ext", "adg-magic", "medium", DOC)
    if ext == ".xls":
        return DetectedType("application/vnd.ms-excel", "OLE2+ext", "adg-magic", "medium", XLS)
    if ext == ".ppt":
        return DetectedType("application/vnd.ms-powerpoint", "OLE2+ext", "adg-magic", "medium", PRESENTATION)
    return DetectedType("application/x-ole-storage", "OLE2", "adg-magic", "low", UNKNOWN, "ole2")


def _sniff_text(head: bytes, ext: str) -> DetectedType | None:
    """文本类嗅探：JSON/XML/HTML/CSV/EML/DXF/纯文本。"""
    try:
        sample = head.decode("utf-8", errors="strict")
        enc = "utf-8"
    except UnicodeDecodeError:
        try:
            sample = head.decode("gb18030", errors="strict")
            enc = "gb18030"
        except UnicodeDecodeError:
            return None
    stripped = sample.lstrip()
    if not stripped:
        return None
    low = stripped[:512].lower()
    if low.startswith(("<!doctype html", "<html", "<head", "<meta")) or ("<html" in low[:2048]):
        return DetectedType("text/html", f"text-sniff({enc})", "adg-magic", "medium", HTML)
    if stripped.startswith("<?xml"):
        return DetectedType("application/xml", f"text-sniff({enc})", "adg-magic", "medium", TEXT, "xml")
    if stripped.startswith(("{", "[")):
        try:
            json.loads(sample)
            return DetectedType("application/json", f"text-sniff({enc})", "adg-magic", "medium", TEXT, "json")
        except (json.JSONDecodeError, ValueError):
            pass
    # EML 头模式
    head_lines = sample.split("\n", 20)[:20]
    header_hits = sum(
        1 for ln in head_lines if ln.startswith(("From:", "To:", "Subject:", "Date:", "Message-ID:", "Received:", "Return-Path:", "MIME-Version:"))
    )
    if header_hits >= 3:
        return DetectedType("message/rfc822", f"header-pattern({header_hits})", "adg-magic", "medium", EMAIL, "eml")
    # DXF：0\nSECTION 或 999 注释
    if ext == ".dxf" or ("\nSECTION" in sample[:4096] and "\nENDSEC" in sample[:65536]):
        return DetectedType("image/vnd.dxf", f"text-sniff({enc})", "adg-magic", "medium", DXF)
    # CSV 启发：多行同列数分隔符
    if ext in (".csv", ".tsv"):
        return DetectedType("text/csv" if ext == ".csv" else "text/tab-separated-values", f"ext+text({enc})", "adg-magic", "medium", CSV, ext[1:])
    if ext in _EXT_HINTS and _EXT_HINTS[ext][0] == TEXT:
        kind, mime, fmt = _EXT_HINTS[ext]
        return DetectedType(mime, f"text-sniff({enc})", "adg-magic", "medium", kind, fmt)
    # 聊天导出弱信号（仅提示，具体由 chat_lite 判定）
    if ext == ".txt":
        return DetectedType("text/plain", f"text-sniff({enc})", "adg-magic", "low", TEXT)
    return DetectedType("text/plain", f"text-sniff({enc})", "adg-magic", "low", TEXT)


def detect(path: Path | str, *, head: bytes | None = None) -> DetectedType:
    """主入口：读取前 64KB 魔数识别，失败回退扩展名低置信判断。"""
    p = Path(path)
    ext = p.suffix.lower()
    if head is None:
        try:
            with open(p, "rb") as f:
                head = f.read(65536)
        except OSError:
            head = b""
    if not head:
        hint = _EXT_HINTS.get(ext)
        if hint:
            return DetectedType(hint[1], "none(empty)", "extension-fallback", "low", hint[0], hint[2])
        return DetectedType("application/octet-stream", "none", "extension-fallback", "low", UNKNOWN)

    # ---- 二进制魔数 ----
    if head.startswith(b"%PDF"):
        return DetectedType("application/pdf", "%PDF", "adg-magic", "high", PDF)
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return _detect_pk_container(p, head)
    if head.startswith(_OLE2):
        return _detect_ole2(ext)
    if head.startswith(b"\xff\xd8\xff"):
        return DetectedType("image/jpeg", "JPEG SOI", "adg-magic", "high", IMAGE, "jpeg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedType("image/png", "PNG", "adg-magic", "high", IMAGE, "png")
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return DetectedType("image/tiff", "TIFF", "adg-magic", "high", IMAGE, "tiff")
    if head.startswith((b"GIF87a", b"GIF89a")):
        return DetectedType("image/gif", "GIF", "adg-magic", "high", IMAGE, "gif")
    if head.startswith(b"BM"):
        return DetectedType("image/bmp", "BMP", "adg-magic", "high", IMAGE, "bmp")
    if head.startswith(b"8BPS"):
        return DetectedType("image/vnd.adobe.photoshop", "PSD", "adg-magic", "high", IMAGE, "psd")
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return DetectedType("image/webp", "RIFF+WEBP", "adg-magic", "high", IMAGE, "webp")
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return DetectedType("audio/wav", "RIFF+WAVE", "adg-magic", "high", AUDIO, "wav")
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return DetectedType("video/x-msvideo", "RIFF+AVI", "adg-magic", "high", VIDEO, "avi")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        kind_mime = _FTYP_BRANDS.get(brand)
        if kind_mime:
            return DetectedType(kind_mime[1], f"ftyp:{brand.decode('latin1')}", "adg-magic", "high", kind_mime[0])
        return DetectedType("video/mp4", "ftyp", "adg-magic", "medium", VIDEO)
    if head.startswith(b"ID3") or (head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF2, 0xFA)):
        return DetectedType("audio/mpeg", "MP3", "adg-magic", "high", AUDIO, "mp3")
    if head.startswith(b"fLaC"):
        return DetectedType("audio/flac", "fLaC", "adg-magic", "high", AUDIO, "flac")
    if head.startswith(b"OggS"):
        return DetectedType("audio/ogg", "OggS", "adg-magic", "high", AUDIO, "ogg")
    if head.startswith(b"Rar!\x1a\x07"):
        return DetectedType("application/vnd.rar", "RAR", "adg-magic", "high", ARCHIVE, "rar")
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return DetectedType("application/x-7z-compressed", "7z", "adg-magic", "high", ARCHIVE, "7z")
    if head.startswith(b"\x1f\x8b"):
        fmt = "tar+gzip" if ext == ".tgz" else "gzip"
        return DetectedType("application/gzip", "gzip", "adg-magic", "high", ARCHIVE, fmt)
    if head[257:262] == b"ustar":
        return DetectedType("application/x-tar", "ustar", "adg-magic", "high", ARCHIVE, "tar")
    if head.startswith(b"SQLite format 3\x00"):
        return DetectedType("application/vnd.sqlite3", "SQLite3", "adg-magic", "high", DATABASE, "sqlite")
    if head.startswith(b"!BDN"):
        fmt = "pst" if ext in (".pst", ".ost") else "pst"
        return DetectedType("application/vnd.ms-outlook-pst", "PST !BDN", "adg-magic", "high", EMAIL, fmt)
    if head.startswith(b"AC10"):
        return DetectedType("image/vnd.dwg", f"DWG {head[:6].decode('latin1')}", "adg-magic", "high", DWG)
    if head.startswith(b"MZ"):
        return DetectedType("application/x-dosexec", "MZ", "adg-magic", "high", EXECUTABLE)
    if head.startswith(b"\x7fELF"):
        return DetectedType("application/x-elf", "ELF", "adg-magic", "high", EXECUTABLE)
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return DetectedType("application/x-mach-binary", "Mach-O", "adg-magic", "high", EXECUTABLE)

    # ---- 文本类嗅探 ----
    text_hit = _sniff_text(head, ext)
    if text_hit is not None:
        return text_hit

    hint = _EXT_HINTS.get(ext)
    if hint:
        return DetectedType(hint[1], "unknown-magic+ext", "extension-fallback", "low", hint[0], hint[2])
    return DetectedType("application/octet-stream", "unknown", "extension-fallback", "low", UNKNOWN)
