"""PPT 旧版（.ppt，CFB 二进制）解析：LibreOffice headless 转 pptx 后复用 pptx_lite。

旧 .ppt 非 OOXML（CFB 二进制），stdlib 无法解析。LibreOffice headless 转为 pptx 后
复用 pptx_lite 提取文本。依赖 LibreOffice（soffice），无则 raise（dependency_missing），
编排器沿降级链转 docling。LO 转换可能损失部分排版/动画；fitness 继承 pptx_lite
（layout_sensitive_analysis=False）。锚点：继承 pptx_lite（slide index）。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import pptx_lite

PARSER_VERSION = "0.1.0"


def _find_soffice() -> str | None:
    for cand in (
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if cand and Path(cand).exists():
            return cand
    return None


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    soffice = _find_soffice()
    if not soffice:
        from gateway.errors import WorkerFailed

        raise WorkerFailed("LibreOffice (soffice) 未找到", kind="dependency_missing")

    with tempfile.TemporaryDirectory(prefix="adg-ppt-old-", dir=str(work_dir)) as outdir:
        # 独立 UserInstallation：避开默认 profile 被其他 soffice 实例占锁导致无限阻塞
        lo_profile = Path(tempfile.gettempdir()) / "adg-lo-profile"
        try:
            proc = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    f"-env:UserInstallation=file://{lo_profile}",
                    "--convert-to", "pptx",
                    "--outdir", outdir,
                    str(source_path),
                ],
                capture_output=True,
                text=True, errors="replace",
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            from gateway.errors import WorkerFailed

            raise WorkerFailed("LibreOffice 转换超时（>300s）", kind="crash")
        if proc.returncode != 0:
            from gateway.errors import WorkerFailed

            raise WorkerFailed(f"soffice 失败 rc={proc.returncode}: {(proc.stderr or '')[-200:]}", kind="crash")

        converted = list(Path(outdir).glob("*.pptx"))
        if not converted:
            from gateway.errors import WorkerFailed

            raise WorkerFailed("LibreOffice 未产出 pptx", kind="crash")
        converted_path = converted[0]

        # 复用 pptx_lite 的全部解析逻辑
        result = pptx_lite.parse(
            converted_path,
            kind="presentation",
            format=format,
            options=options,
            limits=limits,
            work_dir=work_dir,
        )
        result["parser_name"] = "ppt_old"
        result["parser_version"] = PARSER_VERSION
        result["notes"] = ["ppt_old：经 LibreOffice 转 pptx 后由 pptx_lite 提取"] + result.get("notes", [])
        return result
