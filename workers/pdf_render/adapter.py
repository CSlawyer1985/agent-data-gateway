#!/usr/bin/env python3
"""pdf_render worker adapter — ADG Worker Protocol v0.1。

扫描/无文字层 PDF → 页面位图（transform）。位图回灌走 image 链交给 OCR。
存在意义：MinerU 需要 Docker + 模型，本机无容器/无 GPU 时扫描件就彻底读不了；
pypdfium2 是纯 wheel，装上就能把扫描件变成可 OCR 的位图——诚实降级而不是放弃。
"""

import json
import sys
from pathlib import Path

WORKER = {"name": "pdf_render", "version": "0.1.0", "maturity": "implemented", "license": "Apache-2.0"}

DEFAULT_DPI = 200
DEFAULT_MAX_PAGES = 50


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _parse_pages(spec: str, total: int) -> list[int]:
    """"1-5,8" → [0,1,2,3,4,7]（对外 1 起，对内 0 起）。"""
    out: list[int] = []
    for chunk in str(spec).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            out.extend(range(max(1, start), min(total, end) + 1))
        else:
            try:
                n = int(chunk)
            except ValueError:
                continue
            if 1 <= n <= total:
                out.append(n)
    return sorted({n - 1 for n in out})


def handle_parse(req):
    import pypdfium2 as pdfium

    work_dir = Path(req["work_dir"])
    options = req.get("options") or {}
    dpi = int(options.get("dpi", DEFAULT_DPI))
    max_pages = int(options.get("max_pages", DEFAULT_MAX_PAGES))
    password = options.get("password") or None

    try:
        doc = pdfium.PdfDocument(req["source_path"], password=password)
    except Exception as e:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "unsupported", "message": f"PDFium 打开失败（可能需要口令）: {type(e).__name__}: {e}"}}

    total = len(doc)
    if options.get("pages"):
        indices = _parse_pages(options["pages"], total)
    else:
        indices = list(range(total))
    truncated = len(indices) > max_pages
    indices = indices[:max_pages]
    if not indices:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": "没有可渲染的页面"}}

    pages_dir = work_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    output_files = []
    for i in indices:
        page = doc[i]
        image = page.render(scale=scale).to_pil()
        name = f"page_{i + 1:04d}.png"
        image.save(pages_dir / name)
        output_files.append({
            "path": f"pages/{name}",
            "format": "image/png",
            "note": f"PDF 第 {i + 1} 页位图（{dpi} DPI）",
        })

    warnings = [f"渲染 {len(output_files)}/{total} 页为位图（{dpi} DPI）；OCR 结果需对照原件核验"]
    if truncated:
        warnings.append(f"页数超过上限 {max_pages}，其余页未渲染（--worker-option max_pages=N 调整）")
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": output_files,
        "warnings": warnings,
        "metrics": {"pages_total": total, "pages_rendered": len(output_files), "dpi": dpi,
                    "truncated": truncated},
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
              "handles": [{"kind": "pdf", "role": "transform"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except ImportError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "pypdfium2 未安装（adg setup --worker pdf_render）"}}
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
