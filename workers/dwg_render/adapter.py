#!/usr/bin/env python3
"""dwg_render worker adapter — ADG Worker Protocol v0.1。

LibreDWG dwgbmp 把 dwg 渲染成 BMP 位图（transform）。
产物回灌走 image 链，rapidocr 读出图纸里的文字标注（杆塔表、基础详图标注等）。
"""

import json
import subprocess
import sys
from pathlib import Path

WORKER = {"name": "dwg_render", "version": "0.1.0", "maturity": "implemented", "license": "GPL-3.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    work_dir = Path(req["work_dir"])
    src = Path(req["source_path"])
    bmp = work_dir / (src.stem + ".bmp")
    # dwgbmp 渲染（位图，比 SVG→PNG 快且稳定）。
    # 注意：dwgbmp 用法为 `dwgbmp [OPTION]... DWGFILE [thumbnailfile]`，输出文件是位置参数，无 -o 选项。
    proc = subprocess.run(
        ["dwgbmp", str(src), str(bmp)],
        capture_output=True, text=True, timeout=300,
    )
    output_files = []
    warnings = []
    if proc.returncode == 0 and bmp.exists() and bmp.stat().st_size > 0:
        output_files.append({"path": bmp.name, "format": "image/bmp", "note": "LibreDWG 渲染位图，含图纸可视化与文字标注"})
    else:
        stderr = (proc.stderr or "")[-300:]
        if "thumbnail" in stderr.lower() or "no thumbnail" in stderr.lower():
            hint = "该 DWG 无内嵌缩略图，dwgbmp 无法渲染；可尝试 libredwg（dwg2dxf）转 DXF 后另行渲染"
        else:
            hint = stderr or f"dwgbmp 退出码 {proc.returncode}（见 stderr）"
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"dwgbmp 渲染失败: {hint}"}}

    # 可选：同时产 SVG（矢量视图）；若失败不影响主流程
    svg = work_dir / (src.stem + ".svg")
    proc_svg = subprocess.run(
        ["dwg2SVG", str(src)],
        capture_output=True, timeout=300,
    )
    if proc_svg.returncode == 0 and proc_svg.stdout:
        svg.write_bytes(proc_svg.stdout)
        if svg.exists() and svg.stat().st_size > 0:
            output_files.append({"path": svg.name, "format": "image/svg+xml", "note": "矢量视图（供直接查看，不回灌 OCR）"})

    if not output_files:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": "渲染未产出任何图像"}}
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": output_files,
        "warnings": warnings,
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
              "handles": [{"kind": "dwg", "role": "transform"}],
              "requirements": {"min_ram_mb": 512, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "dwgbmp/dwg2SVG 不在 PATH（安装 LibreDWG）"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
