# Agent Data Gateway

> 让 Agent 获得可信、完整、可追溯的数据输入。**摄取编排层**，不是又一个 Document Parser。

律师、文字工作者的真实输入是一个格式极杂的数据包：PDF 扫描件、带批注修订的 docx、财务表、聊天记录、邮件及附件、音视频、工程图纸……当前 Agent 最大的问题不是不会分析，而是**无法可靠地知道自己是否已完整读取全部材料**——文件遗漏、压缩包未展开、OCR 未执行、表格读取失败、来源无法追溯。输入不完整，再强的模型也只建立在残缺事实上。

开源生态（MinerU / Docling / Tika / Whisper / PaddleOCR 等）解决的是 **Document Parsing**（怎么把一个文件解析出来）。本工具做 **Agent Ingestion**——怎么把一个案件材料包可靠地变成 Agent 可消费、可追溯、可信的数据。

## 核心能力

- **完整入账**：递归发现 + 压缩包/邮件安全展开，每个文件都有 item 记录或显式排除记录（`all_items_accounted`）。
- **不可信输入安全模型**：ZIP bomb / 路径穿越 / 符号链接逃逸 / 宏 / PDF JavaScript / worker 资源耗尽——第一版即内置，且所有拦截显性记录。
- **统一数据模型**：`EvidenceDocument`（三身份 source/rendition/run、八维质量 + fitness 适用性、来源锚点、处理谱系）。Markdown/纯文本只是导出视图。
- **特征路由 + 降级链**：按文档特征选最优 parser（如 PDF 数字→Docling、扫描→MinerU），不可用时沿降级链回退并记录原因，绝不假装成功。
- **质量多维化**：一份文件可能可全文检索、可事实提取，但不能可靠引用、不能用于真伪鉴定——`fitness` 旗标如实区分。
- **摄取 ≠ 取证**：提取技术信号（EXIF、哈希、电子签章线索），但 `authenticity_assessment` 恒为 false，真伪鉴定标注"需人工/鉴定复核"。
- **隐私保护**：所有产物默认只含包内相对路径，绝对路径只在本地私有日志。

## 安装

```bash
pip install agent-data-gateway        # 编排器 + Core（纯 stdlib 轻依赖）
# 或开发模式
pip install -e .
```

## 快速开始

```bash
adg check                    # 环境检测：Python/容器/GPU + 各 worker 可用性
adg setup --lite             # 安装轻量 worker（pypdf/trafilatura/ezdxf/extract-msg）
# adg setup --all            # 含重型 worker（docling/mineru/whisper/paddleocr）
adg scan ./案件材料           # 全量发现 → manifest（不解析内容）
adg parse ./案件材料          # 解析（自动特征路由 + 降级）
adg report ./案件材料         # 包级质量报告
adg status ./案件材料         # 哪些文件还没被可靠读取
adg verify                   # 跑验证矩阵自检
```

所有命令 stdout 只打一行摘要，详情写 `<out>/`（默认 `./adg-output/<包名>/`）下的 Markdown/JSON。exit code 三态：`0`=pass / `1`=pass_with_warnings / `2`=fail。

## 与 Agent 的关系

Claude Code 等 Agent 通过 Bash 调 `adg` 子命令（`adg status` 直接回答"哪些没读全"）。`adg parse` 产出的 `EvidenceDocument` JSON 可被 Agent 直接消费；每个元素带来源锚点（页码/坐标/段落/单元格/时间码），引用时可回溯原件。

## 目录结构

```
agent-data-gateway/
├── gateway/          # Core 编排器（纯 stdlib）：发现/调度/质量/锚点/谱系/CLI
│   ├── commands/     # adg check/setup/scan/parse/report/status/list/verify
│   └── selftest.py   # 验证矩阵（程序化 fixtures，无二进制资产）
├── packs/legal/      # Legal Pack：11 个 stdlib lite parser + 预检钩子
├── workers/          # 14 个外部 worker 适配器（docling/mineru/whisper/... 各自 venv）
├── models/           # 冻结 Schema v0.1（EvidenceDocument/Manifest/WorkerProtocol/Quality/Security）
├── docs/spec/        # 10 份规格文档（00-09）
├── input/ scratch/ output/   # 三层目录契约
└── pyproject.toml
```

## 设计原则

1. **不自研 parser**——能用成熟开源 parser 解决的格式绝不重写。项目价值在编排层与模型层。
2. **失败显性化**——不假装成功。读不了的文件进 `unresolved_items` 并给原因与建议。
3. **诚实降级**——lite parser / 兜底产物必须带降级质量标注（如 pdflite：无版面坐标、锚点近似）。
4. **范围分三层**——Core + Legal Pack 进主仓库测试矩阵；CAD/GIS/DICOM 等属 Extended Packs，独立扩展。

## 规范文档

`docs/spec/` 下 00-09 是权威说明；`models/*.v0.1.json` 是机器可读 Schema（normative，冲突时以 Schema 为准）。

## 许可证

Apache-2.0。注意：部分 worker 适配器调用的工具是 GPL/AGPL（extract-msg、LibreDWG、MinerU）——它们隔离为独立进程调用，不链入编排器，详见各 `workers/*/worker.json` 的 `license` 字段。
