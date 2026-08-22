"""自检 fixtures（程序化生成，无二进制资产）与验证矩阵执行器（adg verify）。

fixtures 同时被 tests/ 单元测试复用。矩阵对应 plan §16：
完整性 / 类型识别 / source_id 稳定 / element_id 可重复 / 跨 parser alignment /
特征路由 / 自动降级 / 锚点回溯 / 质量多维+fitness / 安全模型 / 隐私 / worker 隔离 / 环境检测。
"""

from __future__ import annotations

import base64
import io
import json
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

# ---------------- PDF 手工构造 ----------------


def _pdf(objects: list[bytes], trailer_extra: bytes = b"") -> bytes:
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R " + trailer_extra + b" >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    return out


def make_pdf_digital(text: bytes = b"Agent Data Gateway digital text layer sample sentence. ") -> bytes:
    payload = text * 16  # >500 字节文字层
    content = b"BT /F1 18 Tf 72 720 Td (" + payload + b") Tj ET"
    stream = zlib.compress(content)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _pdf(objs)


def make_pdf_scanned() -> bytes:
    content = zlib.compress(b"q 612 0 0 792 0 0 cm /Im1 Do Q")
    image = b"\xff\xff\xff"  # 1x1 RGB 原始像素
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" /Filter /FlateDecode >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\nstream\n" + image + b"\nendstream",
    ]
    return _pdf(objs)


def make_pdf_corrupt() -> bytes:
    return b"%PDF-1.4\nthis is not a real pdf body, no xref, no eof marker\n"


def make_pdf_encrypted() -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -4 >>",
    ]
    return _pdf(objs, b"/Encrypt 4 0 R")


# ---------------- Office 手工构造 ----------------


def make_docx() -> bytes:
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第一章 合同条款</w:t></w:r></w:p>
<w:p><w:r><w:t>本协议由甲方与乙方于 2024 年 1 月 1 日签署，共三页。</w:t></w:r></w:p>
<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>付款方式：银行转账，账期三十天。</w:t></w:r><w:r><w:commentReference w:id="0"/></w:r></w:p>
<w:p><w:ins w:author="张三" w:date="2024-01-02T10:00:00Z"><w:r><w:t>新增条款：违约金按日万分之五计算。</w:t></w:r></w:ins><w:del w:author="张三" w:date="2024-01-02T10:00:00Z"><w:r><w:delText>旧表述：违约金按日千分之一计算。</w:delText></w:r></w:del></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>金额</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>首付款</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>50 万元</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    comments_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:comment w:id="0" w:author="李四" w:date="2024-01-03T09:00:00Z"><w:p><w:r><w:t>请复核付款账户是否为监管账户。</w:t></w:r></w:p></w:comment>
</w:comments>"""
    core_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">
<dc:creator>陈石</dc:creator><dc:title>测试协议</dc:title><dcterms:created>2024-01-01T08:00:00Z</dcterms:created><dcterms:modified>2024-01-03T09:30:00Z</dcterms:modified>
</cp:coreProperties>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""")
        zf.writestr("_rels/.rels", """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""")
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/comments.xml", comments_xml)
        zf.writestr("docProps/core.xml", core_xml)
    return buf.getvalue()


def make_xlsx() -> bytes:
    workbook = """<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="资料" sheetId="1" r:id="rId1"/><sheet name="金额" sheetId="2" r:id="rId2"/></sheets></workbook>"""
    rels = """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>"""
    shared = """<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>名称</t></si><si><t>金额</t></si><si><t>苹果</t></si><si><t>香蕉</t></si></sst>"""
    sheet1 = """<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>100</v></c><c r="C2"><f>B2*2</f><v>200</v></c></row><row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>88</v></c></row></sheetData></worksheet>"""
    sheet2 = """<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>20240101</v></c></row></sheetData></worksheet>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
    return buf.getvalue()


def make_eml(attachment_name: str = "附件说明.txt", attachment_body: bytes = "附件内容：请重点复核第三条付款安排。".encode("utf-8")) -> bytes:
    msg = EmailMessage()
    msg["From"] = "张三 <zhangsan@example.com>"
    msg["To"] = "李四 <lisi@example.com>"
    msg["Subject"] = "合同审阅"
    msg["Date"] = format_datetime(datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc))
    msg["Message-ID"] = "<adg-selftest-001@example.com>"
    msg.set_content("李律师您好：\n\n请审阅附件合同草案，重点是付款条款。\n\n张三")
    msg.add_attachment(attachment_body, maintype="text", subtype="plain", filename=attachment_name)
    return msg.as_bytes()


# ---------------- 压缩包样本 ----------------


def make_zip_normal() -> bytes:
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("c.txt", "嵌套压缩包内层文件 c。")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "压缩包成员 a：证人证言整理。")
        zf.writestr("b.csv", "项目,金额\n律师费,5000\n")
        zf.writestr("内层.zip", nested.getvalue())
    return buf.getvalue()


def make_zip_traversal() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.txt", "路径穿越攻击载荷")
        zf.writestr("ok.txt", "正常成员")
    return buf.getvalue()


def make_zip_symlink() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, "/etc/passwd")
        zf.writestr("ok.txt", "正常成员")
    return buf.getvalue()


def make_zip_bomb(ratio_target: int = 300) -> bytes:
    buf = io.BytesIO()
    payload = b"\x00" * (12 * 1024 * 1024)  # 12MB 零字节，压缩率 >300:1
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.bin", payload)
    return buf.getvalue()


def make_zip_broken() -> bytes:
    return b"PK\x03\x04" + b"\xff" * 64  # PK 头但中央目录损坏


# ---------------- 图像/媒体 ----------------


def make_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    text = b"Author\x00ADG-SelfTest"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", text) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_jpeg_exif() -> bytes:
    def ascii_entry(tag: int, value: bytes, values_offset_base: int) -> tuple[bytes, bytes]:
        data = value + b"\x00"
        if len(data) <= 4:
            field = data.ljust(4, b"\x00")
            return struct.pack("<HHI", tag, 2, len(data)) + field, b""
        entry = struct.pack("<HHII", tag, 2, len(data), values_offset_base)
        return entry, data

    make = b"ADG-Cam"
    model = b"X1"
    dt = b"2024:01:05 10:00:00"
    ifd_count = 3
    entries_len = 2 + ifd_count * 12 + 4
    values_base = 8 + entries_len
    e1, v1 = ascii_entry(0x010F, make, values_base)
    e2, v2 = ascii_entry(0x0110, model, values_base + len(v1))
    e3, v3 = ascii_entry(0x0132, dt, values_base + len(v1) + len(v2))
    ifd = struct.pack("<H", ifd_count) + e1 + e2 + e3 + struct.pack("<I", 0) + v1 + v2 + v3
    tiff = b"II" + struct.pack("<HI", 42, 8) + ifd
    app1 = b"Exif\x00\x00" + tiff
    segment = b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    return b"\xff\xd8" + segment + b"\xff\xd9"


def make_wav(seconds: float = 0.5, rate: int = 8000) -> bytes:
    nframes = int(seconds * rate)
    data = b"\x00\x00" * nframes
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def make_dxf() -> bytes:
    lines = [
        "0", "SECTION", "2", "HEADER",
        "9", "$EXTMIN", "10", "0.0", "20", "0.0", "30", "0.0",
        "9", "$EXTMAX", "10", "100.0", "20", "100.0", "30", "0.0",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "墙体",
        "0", "LAYER", "2", "门窗",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
        "0", "TEXT", "5", "A1", "8", "墙体", "1", "一层平面图",
        "0", "MTEXT", "5", "A2", "8", "门窗", "1", "M100 双扇平开门",
        "0", "LINE", "5", "A3", "8", "墙体",
        "0", "ENDSEC", "0", "EOF",
    ]
    return "\n".join(lines).encode("utf-8")


def make_chat_whatsapp() -> bytes:
    lines = [
        "2024/01/05, 09:30:15 - 张三: 李律师，合同收到了吗？",
        "2024/01/05, 09:31:02 - 李四: 收到了，下午看。",
        "2024/01/05, 09:31:40 - 张三: 好的，重点是付款条款",
        "2024/01/06, 14:05:22 - 李四: 看完了，有三处意见",
        "2024/01/06, 14:06:01 - 李四: 一、违约金比例偏高",
        "2024/01/06, 14:06:33 - 张三: 可以谈",
    ]
    return "\n".join(lines).encode("utf-8")


def make_html() -> bytes:
    return """<!DOCTYPE html><html><head><title>案件新闻</title><script>var x=1;</script></head>
<body><h1>某地工程纠纷开庭</h1><p>今日，某地方法院开庭审理一起建设工程施工合同纠纷。</p>
<p>双方围绕工程量结算展开举证。</p></body></html>""".encode("utf-8")


# ---------------- 包构建 ----------------

PACKAGE_FILES = {
    "合同笔记.txt": "第一步：核对主体。\n\n第二步：核对金额。\n\n第三步：核对签章。".encode("utf-8"),
    "notes.md": "# 案件要点\n\n- 当事人：甲 vs 乙\n\n## 时间线\n\n2024-01-01 签约".encode("utf-8"),
    "财务表.csv": "科目,金额,备注\n律师费,5000,已付\n鉴定费,12000,待付".encode("utf-8"),
    "page.html": make_html(),
    "邮件.eml": make_eml(),
    "bundle.zip": make_zip_normal(),
    "report.docx": make_pdf_digital(b"ADG misnamed-as-docx but actually PDF content for magic test. "),  # 改名测试：实为 PDF（与 digital.pdf 不同字节，防去重）
    "digital.pdf": make_pdf_digital(),
    "scanned.pdf": make_pdf_scanned(),
    "corrupt.pdf": make_pdf_corrupt(),
    "encrypted.pdf": make_pdf_encrypted(),
    "photo.png": make_png(),
    "exif.jpg": make_jpeg_exif(),
    "record.wav": make_wav(),
    "图纸.dxf": make_dxf(),
    "聊天记录.txt": make_chat_whatsapp(),
    "协议.docx": make_docx(),
    "台账.xlsx": make_xlsx(),
    "empty.txt": b"",
    "dup_a.txt": "完全相同的重复内容。".encode("utf-8"),
    "dup_b.txt": "完全相同的重复内容。".encode("utf-8"),
    "broken.zip": make_zip_broken(),
}

THREAT_FILES = {
    "threats/traversal.zip": make_zip_traversal(),
    "threats/symlink.zip": make_zip_symlink(),
    "threats/bomb.zip": make_zip_bomb(),
}

EXPECTED_TOP_FILES = len(PACKAGE_FILES) + len(THREAT_FILES)  # 25
# 完整性期望（手工清点）：
#   discovered = 顶层 25 + bundle 成员 3 + 内层 c.txt 1 + traversal.ok 1 + symlink.ok 1 = 31
#   去重 = dup_a/dup_b(1) + symlink.ok≡traversal.ok(1) = 2
#   items = 31 - 2 = 29
EXPECTED_ITEMS_AFTER_SCAN = 29
EXPECTED_DEDUP = 2


def build_package(root: Path, *, with_threats: bool = True) -> dict[str, bytes]:
    """把完整材料包写入 root，返回 {相对路径: bytes}。"""
    files = dict(PACKAGE_FILES)
    if with_threats:
        files.update(THREAT_FILES)
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return files


# ---------------- 验证矩阵 ----------------


@dataclass
class MatrixRow:
    name: str
    status: str  # pass / fail / skip
    detail: str = ""


@dataclass
class MatrixResult:
    rows: list[MatrixRow] = field(default_factory=list)
    work_dir: Path | None = None

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        status = "skip" if ok is None else ("pass" if ok else "fail")
        self.rows.append(MatrixRow(name=name, status=status, detail=detail))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r.status == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.rows if r.status == "skip")


def run_matrix(work_root: Path | None = None, *, keep: bool = False) -> MatrixResult:
    """执行验证矩阵。work_root 缺省时使用临时目录。"""
    from packs.legal import register_lite_parsers

    from .anchor import validate_document
    from .discover import discover_package
    from .dispatcher import Dispatcher, ParseOptions
    from .hardware import detect_environment
    from .inventory import build_manifest, recompute_package_quality
    from .manifest import documents_dir, list_rendition_docs, load_manifest, rendition_json_path, save_manifest
    from .privacy import contains_absolute_path
    from .workers import WorkerInfo, WorkerRegistry, run_subprocess_protocol
    from packs.legal import chat_kind_refiner, pdf_lite

    result = MatrixResult()
    tmp_ctx = None
    if work_root is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="adg-verify-")
        work_root = Path(tmp_ctx.name)
    work_root.mkdir(parents=True, exist_ok=True)
    result.work_dir = work_root if keep else None

    package_dir = work_root / "案件材料"
    out_dir = work_root / "adg-output" / "案件材料"
    package_dir.mkdir(parents=True, exist_ok=True)
    build_package(package_dir)

    registry = WorkerRegistry()
    register_lite_parsers(registry)
    registry.load_external()
    env = detect_environment()

    bundled_workers = {"docling", "pypdf", "mineru", "py7zr", "whisper"}
    missing_bundled = sorted(bundled_workers - set(registry.workers))
    result.add(
        "发布产物：内置外部 worker 清单可发现",
        not missing_bundled,
        "全部可发现" if not missing_bundled else f"缺失={missing_bundled}",
    )

    # ---------- 环境检测 ----------
    env_dict = env.to_dict()
    need_keys = {"python_version", "os", "cpu_count", "gpu", "container"}
    result.add("环境检测：check 数据齐全", need_keys.issubset(env_dict.keys()), f"python {env_dict.get('python_version')}")

    # ---------- scan ----------
    discovery = discover_package(package_dir, out_dir, kind_refiners=[chat_kind_refiner()])
    manifest = build_manifest(discovery)
    recompute_package_quality(manifest, reliably_read_ids=set(), discovered_only=True)
    save_manifest(manifest, out_dir)

    # 完整性
    result.add(
        "完整性：手工清点 = manifest",
        len(manifest.items) == EXPECTED_ITEMS_AFTER_SCAN
        and manifest.integrity["all_items_accounted"]
        and manifest.stats["dedup_saved"] == EXPECTED_DEDUP,
        f"items={len(manifest.items)} 期望={EXPECTED_ITEMS_AFTER_SCAN} dedup={manifest.stats['dedup_saved']} 期望dedup={EXPECTED_DEDUP}",
    )
    # 类型识别（改名 PDF）
    renamed = next((it for it in manifest.items if it.package_relative_path == "report.docx"), None)
    result.add("类型识别：改名 PDF 按魔数识别", renamed is not None and renamed.kind == "pdf", f"kind={renamed.kind if renamed else '?'}")

    # 安全模型：三个威胁样本
    threat_records = {r["package_relative_path"]: r for r in manifest.integrity.get("expanded_archives", [])}
    trav = threat_records.get("threats/traversal.zip", {})
    trav_rejected = {m["name"] for m in trav.get("rejected_members", [])}
    sym = threat_records.get("threats/symlink.zip", {})
    sym_rejected = {m["name"] for m in sym.get("rejected_members", [])}
    bomb = threat_records.get("threats/bomb.zip", {})
    bomb_rejected = [m for m in bomb.get("rejected_members", [])]
    escaped = (package_dir / "evil.txt").exists() or (work_root / "evil.txt").exists() or (out_dir / "evil.txt").exists()
    result.add(
        "安全模型：路径穿越拒绝且不落地",
        "../evil.txt" in trav_rejected and not escaped,
        f"rejected={sorted(trav_rejected)} escaped={escaped}",
    )
    result.add("安全模型：符号链接拒绝", "link.txt" in sym_rejected, f"rejected={sorted(sym_rejected)}")
    result.add(
        "安全模型：压缩炸弹拒绝",
        any("压缩比" in m["reason"] for m in bomb_rejected),
        f"rejected={[m['reason'] for m in bomb_rejected]}",
    )

    # 特征路由（预检分类）
    f_digital = pdf_lite.preflight_pdf(package_dir / "digital.pdf")
    f_scanned = pdf_lite.preflight_pdf(package_dir / "scanned.pdf")
    f_enc = pdf_lite.preflight_pdf(package_dir / "encrypted.pdf")
    result.add(
        "特征路由：数字/扫描/加密各归其类",
        f_digital.get("pdf_class") == "digital_simple" and f_scanned.get("pdf_class") == "scanned" and f_enc.get("pdf_class") == "encrypted",
        f"digital={f_digital.get('pdf_class')} scanned={f_scanned.get('pdf_class')} enc={f_enc.get('pdf_class')}",
    )

    # ---------- parse ----------
    dispatcher = Dispatcher(package_dir, out_dir, manifest, registry, env=env, options=ParseOptions(force=True, no_abs_log=True))
    summary1 = dispatcher.run()
    manifest = load_manifest(out_dir)

    def doc_of(rel_path: str) -> dict | None:
        item = next((it for it in manifest.items if it.package_relative_path == rel_path), None)
        if not item or not item.renditions:
            return None
        return list_rendition_docs(out_dir, item.source_id)[-1]

    def item_of(rel_path: str):
        return next((it for it in manifest.items if it.package_relative_path == rel_path), None)

    # 邮件附件回灌
    att = item_of("邮件.eml::附件说明.txt")
    result.add("邮件附件递归回灌", att is not None and att.renditions, f"item={'有' if att else '无'}")

    # source_id 稳定
    import hashlib as _hl

    expect_sid = "sha256:" + _hl.sha256(PACKAGE_FILES["合同笔记.txt"]).hexdigest()
    txt_doc = doc_of("合同笔记.txt")
    result.add(
        "L1：source_id 与 parser 无关且稳定",
        txt_doc is not None and txt_doc["source_id"] == expect_sid,
        f"source_id={expect_sid[:23]}…",
    )

    # element_id 可重复（同 parser/版本/配置 重跑）
    docx_item = item_of("协议.docx")
    first_doc = doc_of("协议.docx")
    first_ids = sorted(el["id"] for b in (first_doc or {}).get("content", {}).get("blocks", []) for el in b.get("elements", []))
    first_rid = (first_doc or {}).get("rendition_id")
    dispatcher2 = Dispatcher(package_dir, out_dir, load_manifest(out_dir), registry, env=env, options=ParseOptions(force=True, no_abs_log=True))
    dispatcher2.run()
    manifest = load_manifest(out_dir)
    second_doc = doc_of("协议.docx")
    second_ids = sorted(el["id"] for b in (second_doc or {}).get("content", {}).get("blocks", []) for el in b.get("elements", []))
    second_rid = (second_doc or {}).get("rendition_id")
    result.add(
        "L2：element_id 与 rendition_id 可重复",
        first_ids == second_ids and first_rid == second_rid and len(first_ids) > 0,
        f"elements={len(first_ids)} rid={(first_rid or '')[:23]}…",
    )

    # 自动降级（数字 PDF → 无 docling/pypdf 时落 pdf_lite 且记录降级链）
    docling_ok, _ = (registry.availability(registry.get("docling"), env) if registry.get("docling") else (False, ""))
    pypdf_ok, _ = (registry.availability(registry.get("pypdf"), env) if registry.get("pypdf") else (False, ""))
    pdf_doc = doc_of("digital.pdf")
    if not docling_ok and not pypdf_ok and pdf_doc is not None:
        fell = pdf_doc["parser"].get("fell_back_from") or []
        fell_names = {f["name"] for f in fell}
        result.add(
            "自动降级：首选不可用 → pdf_lite 兜底并记录",
            pdf_doc["parser"]["name"] == "pdf_lite" and {"docling", "pypdf"}.issubset(fell_names),
            f"parser={pdf_doc['parser']['name']} fell_back={sorted(fell_names)}",
        )
    else:
        result.add("自动降级：首选不可用 → pdf_lite 兜底并记录", None, f"docling={'可用' if docling_ok else '不可用'} pypdf={'可用' if pypdf_ok else '不可用'}（链内首选可用，跳过）")

    # 锚点回溯
    anchor_bad = []
    for rel in ("协议.docx", "台账.xlsx", "财务表.csv", "聊天记录.txt", "邮件.eml", "图纸.dxf"):
        d = doc_of(rel)
        if d is None:
            anchor_bad.append(f"{rel}: 无 rendition")
            continue
        problems = validate_document(d)
        if problems:
            anchor_bad.append(f"{rel}: {len(problems)} 处")
    result.add("锚点回溯：抽样元素全部可定位", not anchor_bad, "; ".join(anchor_bad[:3]) or "抽样 6 类格式全部通过")

    # 质量多维 + fitness：定位由 pdf_lite 实际产出的 rendition（corrupt.pdf 走兜底）
    corrupt_doc = doc_of("corrupt.pdf")
    q_pdflite = (corrupt_doc or {}).get("quality", {})
    is_pdflite = (corrupt_doc or {}).get("parser", {}).get("name") == "pdf_lite"
    scanned_item = item_of("scanned.pdf")
    scanned_failed = scanned_item is not None and scanned_item.status in ("failed", "unresolved")
    result.add(
        "质量多维：pdflite 兜底标注诚实降级",
        is_pdflite and q_pdflite.get("layout_preservation") == "fail" and q_pdflite.get("fitness", {}).get("quotation") is False,
        f"parser={(corrupt_doc or {}).get('parser', {}).get('name')} layout={q_pdflite.get('layout_preservation')} quotation={q_pdflite.get('fitness', {}).get('quotation')}",
    )
    enc_paths = set(manifest.quality.get("encrypted", []))
    result.add(
        "质量多维：扫描件失败显性化 + 加密件入清单",
        scanned_failed and "encrypted.pdf" in enc_paths,
        f"scanned={scanned_item.status if scanned_item else '?'} encrypted={sorted(enc_paths)}",
    )
    empty_ok = "empty.txt" in set(manifest.quality.get("empties", []))
    result.add("质量多维：空文件入清单", empty_ok, "")

    # 回归 P2-1：降级链失败时输出完整失败链（此前只留链尾原因，误导排障方向）
    chain_msg = (scanned_item.errors or [""])[0] if scanned_item else ""
    result.add(
        "降级链：失败原因聚合输出",
        scanned_failed and "失败链:" in chain_msg and "mineru" in chain_msg,
        f"msg={chain_msg[:120]}",
    )

    # 回归 P1-1：单文件包 scan + parse（package_root 是文件时路径翻倍曾致误报"原件缺失"）
    sf_root = out_dir.parent / "single-file-pkg"
    sf_root.mkdir(exist_ok=True)
    (sf_root / "协议.docx").write_bytes(make_docx())
    sf_out = out_dir.parent / "single-file-out"
    sf_out.mkdir(exist_ok=True)
    sf_disc = discover_package(sf_root / "协议.docx", sf_out, run_log=None)
    sf_manifest = build_manifest(sf_disc)
    save_manifest(sf_manifest, sf_out)
    sf_disp = Dispatcher(sf_root / "协议.docx", sf_out, sf_manifest, registry, env=env,
                         options=ParseOptions(force=True, no_abs_log=True))
    sf_disp.run()
    sf_item = next((it for it in load_manifest(sf_out).items if it.package_relative_path == "协议.docx"), None)
    result.add(
        "单文件包：scan+parse 路径还原",
        sf_item is not None and sf_item.status == "parsed" and bool(sf_item.renditions),
        f"status={sf_item.status if sf_item else '无 item'} renditions={len(sf_item.renditions) if sf_item else 0}",
    )

    # 跨 parser alignment（officecli 可用时强制第二链）
    oc = registry.get("officecli")
    oc_ok = False
    if oc is not None:
        oc_ok, _ = registry.availability(oc, env)
    if oc_ok:
        m3 = load_manifest(out_dir)
        d3 = Dispatcher(package_dir, out_dir, m3, registry, env=env,
                        options=ParseOptions(force=True, no_abs_log=True, only_kind="docx", chain_override={"docx": ["officecli"]}))
        d3.run()
        manifest = load_manifest(out_dir)
        docx_item = item_of("协议.docx")
        # 对齐表在 .adg/documents/<sid16>/ 下（程序内部哈希寻址），走公共 API 而非拼路径
        align_dir = documents_dir(out_dir, docx_item.source_id)
        align_files = list(align_dir.glob("alignment-*.json"))
        result.add("L3：跨 parser alignment 建立", len(docx_item.renditions) >= 2 and len(align_files) >= 1,
                   f"renditions={len(docx_item.renditions)} alignments={len(align_files)}")
    else:
        result.add("L3：跨 parser alignment 建立", None, "officecli 不可用，跳过")

    # 隐私：产物无绝对路径（logs/ 私有日志除外）
    leaks = []
    for p in out_dir.rglob("*"):
        if not p.is_file() or "logs" in p.parts or p.suffix == ".log":
            continue
        if p.suffix in (".json", ".md"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if contains_absolute_path(text):
                leaks.append(p.name)
    result.add("隐私：产物无绝对路径泄漏", not leaks, f"leaks={leaks[:3]}")

    # worker 隔离：崩溃/超时不击穿编排器
    bad_adapter = work_root / "bad_worker.py"
    bad_adapter.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    bad_w = WorkerInfo(name="bad", version="0", role="parse", kinds=["text"], formats=None,
                       requirements={}, backend="subprocess", adapter_path=bad_adapter)
    r1 = run_subprocess_protocol(bad_w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "t1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": str(work_root)},
                                 timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"])
    slow_adapter = work_root / "slow_worker.py"
    slow_adapter.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    slow_w = WorkerInfo(name="slow", version="0", role="parse", kinds=["text"], formats=None,
                        requirements={}, backend="subprocess", adapter_path=slow_adapter)
    r2 = run_subprocess_protocol(slow_w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "t2", "source_path": "x", "source_id": "", "kind": "text", "work_dir": str(work_root)},
                                 timeout=1, max_output_bytes=1 << 20, env_passthrough=["PATH"])
    result.add(
        "worker 隔离：崩溃与超时可控",
        r1.get("status") == "failed" and r1["error"]["kind"] == "crash" and r2.get("status") == "failed" and r2["error"]["kind"] == "timeout",
        f"crash={r1.get('error', {}).get('kind')} timeout={r2.get('error', {}).get('kind')}",
    )

    # 包级覆盖
    manifest = load_manifest(out_dir)
    cov = manifest.quality.get("extraction_coverage", 0)
    result.add("包级：extraction_coverage 计算", 0 < cov < 1, f"coverage={cov}")

    if tmp_ctx is not None and not keep:
        tmp_ctx.cleanup()
    return result


def render_matrix_md(result: MatrixResult) -> str:
    icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}
    lines = ["# ADG 验证矩阵自检报告", ""]
    passed = sum(1 for r in result.rows if r.status == "pass")
    lines.append(f"通过 {passed} / {len(result.rows)}（失败 {result.failed}，跳过 {result.skipped}）")
    lines.append("")
    lines.append("| 验证项 | 结果 | 详情 |")
    lines.append("|--------|------|------|")
    for r in result.rows:
        lines.append(f"| {r.name} | {icon[r.status]} {r.status} | {r.detail} |")
    lines.append("")
    return "\n".join(lines)
