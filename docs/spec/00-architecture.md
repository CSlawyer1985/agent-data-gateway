# ADG 架构总览（spec 00）

> 本文是 Agent Data Gateway 的架构权威说明。定位：**Agent Ingestion 系统层**，位于原始数据与 Agent 之间，让 Agent 获得可信、完整、可追溯的数据输入。不是 Document Parser——不写 parser，写 Dispatcher + 统一模型 + 质量 + 锚点。

## 架构级边界声明

1. **摄取 ≠ 取证**：本系统提取、验证和报告技术信号（EXIF、电子签章、哈希、元数据），但不能仅凭自动化处理对证据的真实性、合法性或证明力作最终判断。所有技术信号只是线索，标注“需人工/鉴定复核”。
2. **不自研 parser**：能用成熟开源 parser 解决的格式绝不重写。项目价值在编排层与模型层。stdlib lite parser 仅作无依赖兜底，输出必须带降级质量标注。
3. **范围分三层**：主仓库只保证 Core 和 Legal Pack；CAD/DICOM/GIS/科学数据属 Extended Packs，独立扩展，不进核心测试矩阵。

## 范围分层

```text
Core            统一摄取协议、manifest、worker 协议、质量模型、来源锚点
Legal Pack      法律高频格式插件（PDF/DOCX/XLSX/CSV/图片/文本/邮件/聊天/音视频/DXF）
Extended Packs  GIS、DICOM、HDF5/NetCDF、BIM、区块链等（独立扩展包）
```

## 运行形态

- 编排器（gateway/）纯 Python stdlib，零硬依赖，Python ≥ 3.10。
- 重/异构解析走独立隔离 worker：默认 Docker/Podman 容器，兼容本地 subprocess，conda 后置。
- 编排器与 worker 之间只有 Worker Protocol（JSON Lines over stdio），见 spec 04。
- 原件只读：所有输出去 `--out` 指定的输出目录，绝不写回包目录。

## 数据流

```text
包目录 → discover（全量发现+压缩包安全展开）→ inventory（哈希/类型/去重）
→ manifest.json（包级清单）→ dispatcher（特征路由+降级链）
→ worker/lite parser → EvidenceDocument（统一模型）→ 质量定稿 + 锚点
→ 包级报告 → Agent（CLI / 只读 MCP，P5）
```

## 输出纪律

- CLI stdout 只打一行摘要；详情写 Markdown/JSON 文件。
- exit code 三态：0=pass，1=pass_with_warnings，2=fail。
- 所有对外输出只含 package_relative_path（spec 09）。

## 与其他系统的关系

- ALL to MD / MinerU 管线：成熟能力（证据 JSON 设计、印章识别、测试集）后续拆为 adapter/enrichment plugin，不作为架构基础。
- Agent 侧：Claude Code 等通过 Bash 调 `adg` 子命令；P5 提供最小只读 MCP。
