# Changelog

> 迭代日志的详情来源。README「迭代日志」章节保存要点，本文件保存每次迭代的完整记录。
> 约定：每次迭代完成后由 Claude 自动追加本文件 + 同步 README「迭代日志」要点 + 推送。

## v0.1.0（2026-08-05）

### 初始发布

- 编排器核心（gateway/，纯 stdlib）：发现、安全展开、类型识别、特征路由、调度、质量定稿、锚点派生、谱系记录、渲染输出、CLI 九子命令
- Legal Pack（packs/legal/）：11 个 stdlib lite parser + PDF 预检钩子 + 聊天导出识别
- 外部 worker 适配器 ×14（workers/）：docling、mineru、pdfplumber、pypdf、qpdf、rapidocr、paddleocr、whisper、video_frames、xls、officecli、tika、ezdxf、extract_msg、libredwg、dwg_render、libpff、py7zr、trafilatura、av_lite（各自独立 venv / docker）
- 冻结 Schema v0.1 ×5（models/）：EvidenceDocument、PackageManifest、WorkerProtocol、QualityDimensions、SecurityPolicy
- 规格文档 ×10（docs/spec/00-09）
- 验证矩阵（gateway/selftest.py）：程序化 fixtures，`adg verify` 一键自检
- 三层目录契约（input/ scratch/ output/）

### 健壮性测试与修复（真实混合数据包：25 文件 / 13 类型 / 嵌套目录 / 压缩包 / 音视频）

修复清单：

| # | 级别 | 问题 | 修复 |
|---|------|------|------|
| P1-1 | 功能 | 单文件包 scan 后 parse 必失败：package_root 传文件路径导致 `resolve_source_path` 路径翻倍，误报"原件缺失" | `resolve_source_path` 对单文件 package_root 以父目录为根还原 |
| P1-2 | 功能 | dwgbmp 适配器传不存在的 `-o` 选项（输出文件是位置参数），DWG 位图渲染必然失败；无缩略图文件错误信息不可操作 | 改位置参数；区分"无内嵌缩略图"提示，指引 libredwg 转 DXF 路径 |
| P2-1 | 诊断 | 降级链失败原因只保留链尾（如 .ppt 真实失败是 LibreOffice 超时，只显示 docling 镜像未构建，误导排障） | `fell_back` 记录每级 detail，输出"失败链: A: 原因 → B: 原因" |
| P3-1 | 能力 | PSD（8BPS 魔数）识别为 unknown，律师场景常见格式无法处理 | magic 表新增 PSD → image 链，实测 rapidocr 成功解析 |
| P3-2 | 展示 | 渲染物 MD 头部类型显示 "?"：Item 序列化丢失 scan 阶段 detected_type | `to_manifest_entry` 输出 detected_type/mtime |
| P3-3 | 语义 | scan 阶段 manifest 标 fail 而 exit code=1（空 reliably_read_ids 触发"全灭才 fail"分支） | `recompute_package_quality` 新增 `discovered_only`，scan 阶段语义对齐 |
| P3-4 | 测试 | verify L3 alignment 假失败：断言路径少了 `.adg/` 前缀 | 改用 `documents_dir` 公共 API；对齐功能本身正常 |
| 隐私 | 回归 | P2-1 的 detail 含 worker 异常消息里的绝对路径（如 /private/tmp/...），随产物落盘 | `scrub_text` 覆盖 /var/folders、/private/tmp、/tmp；dispatcher 落盘前统一 scrub |

### 验证结果

- `adg verify`：17/19 → **20/21 通过，0 失败**（1 跳过为环境依赖项：docling 不可用但链内 pypdf 可用）
- 新增回归用例 ×2：单文件包 scan+parse 路径还原；降级链失败原因聚合输出
- input 真实混合包回归：25 项全入账、覆盖率 79.4%、未解决 5→4（PSD 转"已解析 pass"）、失败链信息完整可诊断、产物零绝对路径泄漏
