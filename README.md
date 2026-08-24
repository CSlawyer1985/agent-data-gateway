# Agent Data Gateway

<p align="center">
  <a href="https://github.com/CSlawyer1985/agent-data-gateway/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://github.com/CSlawyer1985/agent-data-gateway"><img src="https://img.shields.io/badge/version-v0.2.4-brightgreen" alt="Version"></a>
  <a href="https://github.com/CSlawyer1985/agent-data-gateway"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
  <br>
  <b>让 Agent 获得可信、完整、可追溯的数据输入</b>
  <br>
  <b>摄取编排层（Agent Ingestion），不是又一个 Document Parser</b>
  <br>
  合同 · 证据 · 判决书 · 财务表 · 聊天记录 · 邮件 · 音视频 · 图纸 · 压缩包
</p>

---

> **新用户？** 从 [快速开始](#快速开始) 开始——三个命令跑通一个材料包。本文是完整参考手册。
>
> **核心承诺：不假装成功。** 读不了的文件进 `unresolved_items` 并给出原因与建议；本地解决不了的问题显性失败，绝不静默降质。

---

## 为什么做

律师、文字工作者的真实输入是一个格式极杂的数据包：PDF 扫描件、带批注修订的 docx、财务表、聊天记录、邮件及附件、音视频、工程图纸……

当前 Agent 最大的问题不是不会分析，而是**无法可靠地知道自己是否已完整读取全部材料**：

| 现实问题 | 后果 |
|---------|------|
| 文件遗漏（压缩包未展开、附件未拆出） | 分析建立在残缺事实上 |
| OCR 未执行（扫描件当成空文件） | "无文字层"被误读为"无内容" |
| 表格读取失败（多工作表、合并单元格） | 关键数字缺失无人知晓 |
| 解析失败被静默跳过 | 无法区分"读全了"与"读不了" |
| 来源无法回溯 | 引用时找不到原文位置 |
| 解析器悄悄换成了低质量兜底 | 报告看起来正常，实际不可引用 |

开源生态（MinerU / Docling / Tika / Whisper / PaddleOCR 等）解决的是 **Document Parsing**——怎么把一个文件解析出来。本工具做 **Agent Ingestion**——怎么把一个案件材料包**可靠地**变成 Agent 可消费、可追溯、可信的数据。

## 当前状态（v0.2.4）

| 维度 | 状态 |
|------|------|
| **版本** | v0.2.4（2026-08-24）；v0.1.0 于 2026-08-05 首次发布 |
| **编排器核心** | ✅ 完整可用：发现/安全展开/类型识别/特征路由/调度/质量/锚点/谱系/渲染，纯 stdlib |
| **Legal Pack** | ✅ 11 个 lite parser + 预检钩子已注册 |
| **外部 worker** | ✅ 24 个适配器随 wheel 发布（含文档 VLM / anydoc / pdf_render / dwgread），按环境安装；可用性以本机 `adg check` 为准 |
| **Agent 接口** | ✅ 元素级检索索引 + `adg index/search/anchor/get`：命中带来源锚点与可引用标注，纯 stdlib |
| **验证矩阵** | ✅ `adg verify` 27/28 通过、0 失败（1 跳过为环境依赖项）；跨 parser 对齐（L3）已用真实材料验证 |
| **真实数据验证** | ✅ input 混合材料包（23 物理文件 → 展开/派生后 41 items）：全量入账、覆盖率 **97.6%**、仅剩 1 项未解决（CAJ 属知网专有格式，无开源解析器），带原因与可执行建议 |
| **资料利用率** | 📋 设计已定稿未实现：增益轨（多视角并行）+ 信号探测度量 + 快速/均衡/质量三档成本控制（[ADR 0002](docs/adr/0002-utilization-as-first-class-goal.md) / [spec 11](docs/spec/11-utilization.md)） |
| **云端扩展** | 📋 设计预留（远程 worker 后端、云端模型路由），未实现 |
| **Extended Packs** | 📋 规划（CAD 深入/GIS/DICOM 等），未发布 |

已知限制与已知问题见下方「迭代日志」最新条目；全部规格以 `docs/spec/` 与 `models/*.v0.1.json` 为准。

## 目标

1. **完整入账**：包内每个文件都有 item 记录或显式排除记录（`all_items_accounted`），"读全了"是可验证的事实而非感觉。
2. **失败显性化**：任何文件未能可靠读取，必须带着阶段、原因、建议出现在清单里——Agent 和律师都知道"哪里还没读全"。
3. **诚实降级**：解析器沿降级链回退时，产物必须携带质量标注（如 `layout=fail`、`quotation=False`），让下游知道"这份内容能检索、但不能可靠引用"。
4. **提高资料利用率**（设计中）：同一份材料用多个工具从多个角度处理——文字视角、视觉视角、容器视角、密码学视角——把"一个工具只利用了 60%"提到尽可能高，并如实报告还有多少信号没提取。见 [spec 11](docs/spec/11-utilization.md)。
5. **本地最优**：默认全链路本地处理，客户材料、律师执业秘密不出本机。
6. **云端可扩**：本地无解时（无 GPU、缺重型模型、格式无本地 parser），沿显式授权路径扩展云端服务与模型，产物与来源标注规则不变。
7. **摄取 ≠ 取证**：提取技术信号（EXIF、哈希、电子签章线索），但真伪鉴定恒为 `false`，标注"需人工/鉴定复核"。

## 实现方法

### 架构总览

```
原始材料包（不可信输入）
        │
        ▼
┌─────────────────────────────────────────────────┐
│  ADG 编排器（gateway/，纯 stdlib）               │
│  发现 → 安全展开 → 类型识别 → 特征路由 → 调度      │
│  → 质量定稿 → 锚点派生 → 谱系记录 → 渲染输出       │
│                                                   │
│  ├─ Worker 协议：JSON Lines over stdio（spec 04） │
│  ├─ 降级链：特征路由 + 诚实回退（spec 08）         │
│  ├─ 安全模型：ZIP bomb / 路径穿越 / 符号链接 /      │
│  │            宏 / PDF JS / 资源耗尽（spec 06）    │
│  └─ 隐私：产物只含包内相对路径（spec 09）           │
└─────────────────────────────────────────────────┘
        │
        ▼
  EvidenceDocument（统一模型，spec 02）
  三身份 source/rendition/run + 八维质量 + fitness + 锚点 + 谱系
```

### 核心机制

| 机制 | 实现 | 解决的问题 |
|------|------|-----------|
| **完整入账** | 递归发现 + 压缩包/邮件安全展开，每文件必有 item 或排除记录 | 文件遗漏无人知晓 |
| **不可信输入安全模型** | ZIP bomb / 路径穿越 / 符号链接逃逸 / 宏 / PDF JavaScript / worker 资源耗尽，第一版即内置，拦截显性记录 | 恶意或损坏材料击穿管线 |
| **三身份模型** | `source_id`（原件，跨 parser 稳定）/ `rendition_id`（解析产物）/ `run_id`（执行） | parser 升级后结果对比与追溯 |
| **特征路由 + 降级链** | `gateway.routing.ROUTE_RULES` 同时驱动调度与 `adg routes` 总表；按文档特征选最优 parser，不可用沿链回退并记录每级失败原因 | 拿最优工具处理最优场景，失败原因可诊断，文档不与实现漂移 |
| **质量多维化** | 八维质量（发现/类型/文本/版面/OCR/附件/元数据/锚定）+ fitness 五旗标（可检索/可提取/可引用/版面敏感/真伪鉴定） | 一份文件"能检索 ≠ 能引用"如实区分 |
| **来源锚点** | 元素级定位：页码/坐标/表格单元/时间码/字段，引用可回溯原件 | 结论无法定位到证据位置 |
| **处理谱系** | 每次解析记录 operation/tool/exit_code/哈希，链式可查 | 产物来源与处理历史不可追溯 |
| **隐私保护** | 产物默认只含包内相对路径，绝对路径只在本地私有日志；输出前 scrub 兜底清洗 | 客户名、目录结构、磁盘位置不泄露 |
| **检索与回溯** | 元素级 SQLite/FTS5 索引（中文逐字切分）+ `adg anchor` 回溯；命中带 `quotable` 标注 | Agent 查得到、引得回，且知道哪句不能引 |

## 格式范围与总路由表

项目不再在 README 里手工复制一份容易过期的 worker 链。实际调度和总表共用 `gateway.routing.ROUTE_RULES`：

```bash
adg routes                       # 全部：扩展名、触发条件、动作、工具链、选择原因
adg routes --kind pdf            # 查看 PDF 的加密/损坏/扫描/混合/数字分流
adg routes --format .msg --json  # 查看单个格式，或让 Agent 读取 JSON
adg check                        # 验证这些 worker 在当前机器是否真的可用
```

总表会区分 `parse`（内容解析）、`expand`（容器安全展开）和 `unresolved`（可识别但当前没有安全处理路径）。因此“识别到 `.rar`”不会被误报为“已经支持解析”；未知格式也不会猜测工具后假装成功。完整语义见 [路由策略](docs/spec/08-routing.md)。

## 本地最优：数据不出本机

律师材料的默认边界：**客户秘密与执业秘密在本地闭环内处理**。

- **worker 隔离**：Docker/Podman 才提供只读挂载、`network=none` 与资源限额；subprocess/独立 venv 只隔离依赖、崩溃和超时，仍继承当前用户的文件与网络权限，因此只运行受信任的适配器和依赖。
- **隐私三规则**（spec 09）：产物只出现包内相对路径；绝对路径只进本地私有日志（可 `--no-abs-log` 关闭）；输出前兜底 scrub。
- **网络边界**：需要网络的 worker 禁止使用 subprocess，必须进入能执行网络策略的容器并显式说明用途；`network=false` 对 subprocess 是受信任代码纪律，不冒充操作系统防火墙。
- **本地能力自查**：`adg check` 一键列出本机 25+ worker 可用性，`adg doctor` 按实际材料诊断缺口，`adg setup` 一键补装。

## 云端扩展：本地无路径时

本地没有解析路径（无 GPU 跑不了重型模型、缺特定依赖、格式无本地 parser、或超大批量需要算力）时，沿以下设计路径扩展云端服务与模型。**扩展不是默认动作——需要显式配置与授权。**

| 扩展点 | 机制 | 状态 |
|--------|------|------|
| **远程 worker 后端** | Worker 协议是统一的 JSON Lines over stdio 接口（spec 04），执行后端（docker/subprocess/inproc）是声明式可插拔的——新增 `remote` 后端（SSH/HTTP/云函数）即可把任意解析服务接入降级链，协议与产物规则不变 | 设计预留 |
| **云端模型路由** | 降级链末端挂 `remote_xxx` 占位：本地链全失败时显性转向云端，产物同样过质量定稿与 fitness 标注 | 设计预留 |
| **网络声明纪律** | `network=true` 的 worker 必须说明用途；涉密材料默认禁止外发，云端路径需用户显式授权 | 已内置 |
| **结果回灌一致性** | 云端产物回灌后走同一套锚点派生、哈希、谱系记录——来源是本地还是云端不影响模型与追溯规则 | 已内置 |

**云端扩展的隐私边界**：涉及客户秘密、执业秘密的材料，默认不配置云端路径；确需云端的，由用户在材料包级别显式开启，并承担相应合规判断。

## 安装

推荐把 ADG 作为独立命令行工具安装，避免和项目里的 Python 依赖互相影响：

```bash
uv tool install agent-data-gateway
adg --version
```

没有安装 `uv` 也可以直接使用 `pip`：

```bash
python3 -m pip install agent-data-gateway
```

包已发布到 [PyPI](https://pypi.org/project/agent-data-gateway/)。上面安装的是编排器和零硬依赖的 Core；
要启用更多文件格式，再执行：

```bash
adg setup --auto
adg check
```

`adg setup --auto` 会自动完成两件事：

1. **自动选线路**——并发测速 pypi 官方 / 清华 / 阿里 / 腾讯 / 中科大，选最快的那个（结果缓存 7 天）。
   直连官方就用官方，不硬塞镜像。显式 `--index-url` 或环境变量 `ADG_PIP_INDEX_URL` 永远优先。
2. **只装该装的**——收无系统依赖、体积可接受的 worker；需要 Docker 或数 GB 模型的（文档 VLM、docling/mineru/paddleocr/whisper 等）
   不进一键档，装完会告诉你它们各自的一行安装命令。准入写在各 `worker.json` 的 `auto_install`，不是代码里的硬编码名单。

### 模型会自动安装吗？

**分情况，不会在安装 CLI 时把所有模型都下载下来。**

- `uv tool install` / `pip install` 只安装 ADG Core，不下载模型。
- `adg setup --auto` 安装轻量自动档 worker；其中 RapidOCR 的模型随依赖包一起安装，无需首次运行再联网下载。
- Whisper、PaddleOCR、Docling、MinerU 等重型 worker 默认不进自动档，需要时再按提示显式安装。
- 对声明了模型预取步骤的 worker（包括 Whisper、PaddleOCR、PaddleOCR-VL、OvisOCR2），执行 `adg setup --worker <名称>`，并备齐 FFmpeg 等系统依赖后，
  ADG 会自动预取模型；加 `--no-models` 可以跳过。若预取失败，ADG 会明确警告；运行时能否重试下载由对应上游决定，
  其中 OvisOCR2 明确只读本地缓存，需重新执行 setup 补齐权重。

例如，需要音视频转写时再安装 Whisper：

```bash
adg setup --worker whisper
```

### 复杂 PDF / 文档页图模型

文章中实际对比的主流方案已接入统一 Worker 协议。它们都不随 Core 安装，按需要选择：

| Worker | 输入 | 安装/启动 | ADG 输出 |
|---|---|---|---|
| `paddleocr_vl` | PDF、图片 | `adg setup --worker paddleocr_vl` | 逐页 Markdown，页级锚点 |
| `ovisocr2` | 单页图片 | `adg setup --worker ovisocr2` | 自然阅读顺序 Markdown；PDF 先由 `pdf_render` 拆页 |
| `mineru` | PDF | 既有 `mineru` Worker；按其清单准备 CLI/Docker 与模型 | 页面、段落、表格、图片及 bbox |

直接指定某个模型测试：

```bash
adg parse ./材料 --chain pdf=paddleocr_vl
adg parse ./材料 --chain image=ovisocr2
```

两个新 worker 尚处 `protocol-smoke`：`balanced` 不把它们放在已验证工具之前；`quality` 才额外执行
PaddleOCR-VL/OvisOCR2 独立视角，也可用上面的 `--chain` 显式试跑。这里的“并跑”表示独立产物语义，
当前调度器按链顺序执行，不承诺墙钟时间并发。

装完会打印一张「本机现在能读什么、还差什么、缺的那个怎么补」的表——**装到什么程度，就诚实报到什么程度**。

> **运行前提**：本机有 Python 3.10+（实际部署统一 3.12）并能开终端。这不是待补的门槛——
> `adg` 的调用者是**开发机上的编码 Agent**（Claude Code、Codex 这类），其宿主本就具备。
> 单文件可执行（免 Python）与图形界面**已决定不做**，PyInstaller 的实测否决依据见
> [ADR 0003](docs/adr/0003-distribution-and-target-runtime.md)。

开发模式：`pip install -e .`

## 快速开始

```bash
adg setup --auto             # 自动选源 + 安装轻量自动档 worker
adg check                    # 环境检测：Python/容器/GPU + 各 worker 可用性
adg routes                   # 总路由表：格式 → 条件 → 工具 → 原因
# adg setup --worker whisper # 按需单装重型 worker（音视频转写）
adg scan ./案件材料           # 全量发现 → manifest（不解析内容）
adg parse ./案件材料          # 解析（自动特征路由 + 降级）
adg report ./案件材料         # 包级质量报告
adg status ./案件材料         # 哪些文件还没被可靠读取

adg search ./案件材料 "担保 效力"   # 元素级检索（命中带页码/坐标/单元格/时间码锚点）
adg anchor ./案件材料 el_45f85d56e61a  # 把命中回溯到原件位置，确认能不能引用
adg get ./案件材料 "合同.pdf"        # 取出解析产物、质量八维与降级链

adg verify                   # 跑验证矩阵自检
```

`adg parse` 结束后会顺手建好检索索引（`--no-index` 可关），因此 `search/anchor` 开箱即用。

所有命令 stdout 只打一行摘要，详情写 `<out>/`（默认 `./adg-output/<包名>/`）下的 Markdown/JSON。exit code 三态：`0`=pass / `1`=pass_with_warnings / `2`=fail。

## 与 Agent 的关系

ADG 是**给 Agent 用的命令行工具**：Claude Code 等 Agent 经 Bash 调子命令，每个命令加 `--json`
就是可直接消费的结构化结果——`adg status` 回答"哪些没读全"，`adg search` 检索，`adg anchor` 回溯，
`adg get` 取产物。全部查询走同一份 `gateway.agent_api` 实现，同一问题不会有两种答案。

三条诚实语义贯穿所有查询：

- **检索范围只覆盖已可靠读取的材料**，结果里带 `coverage_caveat` 提示还有几项没读全——"没搜到"不等于"不存在"。
- **每条命中带 `quotable`**：来自该 rendition 的 `fitness.quotation`。false 表示能检索、能作线索，但锚点不足以可靠引用。
- **每条命中可回溯**：`resolve_anchor(element_id)` 给出原件路径、parser 与版本、页码/坐标/单元格/时间码及前后文。

**由使用者主动调用**：ADG 不往项目里装 skill、不写 `CLAUDE.md` / `AGENTS.md` 片段、不做 MCP server，
也不试图让 Agent 自动发现自己。需要用它的人自己让 Agent 去跑 `adg`——工具负责在被调用时给出诚实结果，
不负责推销自己（[ADR 0003](docs/adr/0003-distribution-and-target-runtime.md) 决策 6）。

**但被调用之后必须能被读懂**——被误读的诚实结果不是诚实结果。`adg --help` 因此承载三件事：
典型流程顺序、退出码三态（**`1` 是「完成但有未读全项」，不是失败**，而它是常态）、
以及检索范围只覆盖已可靠读取的材料。这些承诺由测试与真实退出码绑定，不允许漂移
（[ADR 0003](docs/adr/0003-distribution-and-target-runtime.md) 决策 7）。

## 目录结构

```
agent-data-gateway/
├── gateway/          # Core 编排器（纯 stdlib）：发现/调度/质量/锚点/谱系/CLI
│   ├── commands/     # adg check/doctor/setup/scan/parse/report/status/list/index/search/anchor/get/routes/verify
│   ├── index.py      # 元素级检索索引（SQLite + FTS5，中文逐字切分）
│   ├── agent_api.py  # 六个只读查询操作的唯一实现（各查询子命令共用）
│   └── selftest.py   # 验证矩阵（程序化 fixtures，无二进制资产）
├── packs/legal/      # Legal Pack：11 个 stdlib lite parser + 预检钩子
├── workers/          # 24 个外部 worker 适配器（含文档 VLM、OCR、Office、CAD、音视频等；各自隔离）
├── models/           # 冻结 Schema v0.1（EvidenceDocument/Manifest/WorkerProtocol/Quality/Security）
├── docs/spec/        # 12 份规格文档（00-11）
├── docs/adr/         # 契约权威、写入所有权、分发形态等架构决策记录
├── input/ scratch/ output/   # 三层目录契约
└── pyproject.toml
```

## 设计原则

1. **不自研 parser**——能用成熟开源 parser 解决的格式绝不重写。项目价值在编排层与模型层。
2. **失败显性化**——不假装成功。读不了的文件进 `unresolved_items` 并给原因与建议。
3. **诚实降级**——lite parser / 兜底产物必须带降级质量标注（如 pdflite：无版面坐标、锚点近似）。
4. **范围分三层**——Core + Legal Pack 进主仓库测试矩阵；CAD/GIS/DICOM 等属 Extended Packs，独立扩展。
5. **本地最优、云端可扩**——本地能解决的绝不上云；本地无解时沿显式授权路径扩展，产物规则不变。

## 规范文档

`docs/spec/` 下 00-11 是权威说明；`models/*.v0.1.json` 是机器可读 Schema（normative，冲突时以 Schema 为准）。

做到哪了、接着做什么，见 [`docs/roadmap.md`](docs/roadmap.md)（含开发环境与几个已知的坑）。

架构决策记录在 `docs/adr/`——**读这个项目（人或 AI）应先看这三份，它们解释了「为什么是这样」**：

| ADR | 决定了什么 |
|---|---|
| [0001 契约权威与写入所有权](docs/adr/0001-contract-authority-and-write-ownership.md) | 谁拥有哪个事实的定稿权；Schema / spec / README 冲突时以谁为准；同一输出目录单写者 |
| [0002 资料利用率作为一等目标](docs/adr/0002-utilization-as-first-class-goal.md) | 完整性之外并列利用率；降级链（or）之外增加增益轨（and）。**设计已定稿，实现未开始** |
| [0003 分发形态与目标运行环境](docs/adr/0003-distribution-and-target-runtime.md) | 面向开发机上的编码 Agent，走 pip / uv tool 分发；冻结打包已实测否决；venv 落 `~/.adg`；不做 Agent 自动发现入口 |
| [0004 利用率不是一个可计算的比值](docs/adr/0004-utilization-is-not-a-ratio.md) | 撤销利用率度量：分子需要 ground truth，有 ground truth 就不需要这些工具。改输出「探测到什么 + 跑过什么」两组事实，不做除法、不评判结果好坏 |
| [0005 工作流由 profile 选择](docs/adr/0005-workflow-selection-by-profile.md) | 机制与策略分离（Linux "mechanism, not policy"）：ADG 提供识别/路由/执行/记录，跑几条链与哪个结果可用由调用方经 `--profile` 决定。取代 ADR 0002 的「信号缺口驱动」，不新增名词、不加第四种 action |

## 迭代日志

> **自动同步约定**：每次迭代（功能新增、缺陷修复、验证结果）完成后，由 Claude 自动在本节追加条目并推送。条目格式：`日期 | 版本 | 类型 | 要点`。历史详情见 `CHANGELOG.md`。

| 日期 | 版本 | 类型 | 要点 |
|------|------|------|------|
| 2026-08-24 | v0.2.4 | 锚点/拦截修复 | element_id 派生加入 source_id，anchor 回溯唯一指回原件（此前同 parser 同 locator 跨文件共享 ID，真实包一个 ID 最坏横跨 15 个源文件）；压缩包成员安全拦截显性化：被拦截容器不再计入可靠读取，明细进 status/manifest.md/report.md；status 与 report 的「哪些没读全」口径统一（补包级排除）；`.ost` 识别、docx_lite attachments 维度、csv_lite locator 小修复。已有产物需 `adg parse --force` 重跑生效 |
| 2026-08-24 | v0.2.3 | 真实安装回归修复 | 修复 PaddleOCR 3.x、重型模块冷导入误报、VLM 脏 stdout、Ovis 缺 `torchvision`、`file://` 路径与尾部生成循环；图片占位符、OCR 多数低置信、截断/重复内容不再冒充可事实提取或可引用，可靠覆盖率为 0 的包不再报 pass。 |
| 2026-08-24 | v0.2.2 | PDF/OCR 与契约修复 | 接入 PaddleOCR-VL-1.6、OvisOCR2；真实模型验收前标记 `protocol-smoke`，仅进入 quality/显式链。补完整 Schema 落盘前录取、`low_quality` 降级、统一硬件判定、subprocess 真实安全边界、运行依赖谱系与 wheel 发布门禁。 |
| 2026-08-05 | v0.1.0 | 初始发布 | 编排器核心 + Legal Pack + 14 worker 适配器 + 10 份 spec + 验证矩阵 |
| 2026-08-05 | v0.1.0 | 修复 | 真实混合数据包健壮性测试后：①单文件包 scan+parse 路径还原（P1-1）②dwgbmp 位置参数与无缩略图提示（P1-2）③降级链失败原因逐级聚合输出（P2-1）④PSD 魔数识别 ⑤detected_type/mtime 落盘（渲染物类型显示）⑥scan 阶段状态与 exit code 语义对齐 ⑦verify L3 alignment 路径修正 ⑧产物路径 scrub 覆盖 /private/tmp 与 /var/folders（隐私回归） |
| 2026-08-05 | v0.1.0 | 验证 | `adg verify` 17/19 → 20/21（含新增回归用例：单文件包、失败链聚合）；input 混合包未解决项 5→4，PSD 从"类型无法识别"转为"已解析 pass" |
| 2026-08-22 | v0.2.0 | 安全/一致性修复 | Worker 协议与产物路径严格录取；Manifest/Evidence Schema+哈希+身份读取校验；单写者锁、陈旧写入检测与孤儿 rendition 恢复；locator、图片 fitness、force 刷新、质量缓存、derived_from 和 wheel Worker 打包修复；源码 87 项测试、真实材料包与独立安装验证通过 |
| 2026-08-23 | v0.2.0 | 缺陷/校准 | officecli 适配器实测校准：补 `--json`、按 `{success,data.results}` 解析、正文改段落级锚点、批注/修订经 anchoredTo 挂回段落；**查询失败不再退化成「0 条」而是显性失败**。同时修正 docx fixture 为合规 OOXML（此前缺 `document.xml.rels` 与 Content_Types 声明，Word 与任何规范实现都读不到那条批注，验证矩阵在测一个假场景）。L3 跨 parser 对齐首次真实通过：916 元素配对 score=1.0 |
| 2026-08-23 | v0.2.0 | 能力/缺陷 | DWG 改为 dwgread 直读（dwg2dxf 中转的 DXF 结构性损坏，ezdxf.recover 亦拒绝加载），真实图纸提出 9777 条文字并带图层/句柄锚点；修复所有 adapter 用 UTF-8 硬解子进程输出（中文工具输出 GBK 一个字节即崩）。覆盖率 95.1%→**97.6%**，仅剩 CAJ 一项 |
| 2026-08-23 | v0.2.0 | 设计决策 | 增益轨的触发方式改为 profile 驱动（ADR 0005 取代 ADR 0002 决策 2 的缺口驱动）：`parse` 从「跑一条链到成功」扩展为「按 `--profile fast\|balanced\|quality` 跑 N 条链」，链内仍 `or`、链间可选 `and`，各自产出独立 rendition。不新增「工作流」名词（链本身就是），不加第四种 action，去掉「信号↔能力」映射——净变化是减少。机制与策略分离：ADG 不拥有「哪个结果更好」，链的优先级来自开发期离线评估，运行时不评判 |
| 2026-08-23 | v0.2.0 | 设计修正 | 撤销「资料利用率」作为比值度量（ADR 0004 修订 ADR 0002 决策 1）：分子「已成功提取的信号数」需要 ground truth，而有 ground truth 就不必调用这些解析工具——原理上不可知，实现到接分子时暴露。改为输出「探测到哪些信号（三态）+ 实际跑过哪些工具」两组并列事实，不做除法、不合成评分，哪个结果好由调用方判断。增益轨方向不变，被推翻的只是给它打分的尺子 |
| 2026-08-23 | v0.2.0 | 设计决策 | 确立分发形态与目标运行环境：调用者是开发机上的编码 Agent，分发走 `pip`/`uv tool`，不做 `.app`/图形界面/代码签名。PyInstaller 冻结**实测否决**——能打出 20MB 单文件并发现 36 个 worker，但冻结二进制无 `venv`/`ensurepip` 装不了三方包，且 onefile 每次解包到新临时目录、装了下次即失。worker venv 落点随之移出 `site-packages`，改由 `ADG_HOME`（默认 `~/.adg`）承载。ADR 0003 |
| 2026-08-22 | v0.2.0 | 能力/缺陷 | 加密 PDF 先试空用户口令（多数只是所有者限制，此前一律谎报"需密码"）；扫描 PDF 新增 pdf_render 本地位图链（无需 Docker/GPU）；接入 anydoc（Rust/MIT）补齐 RTF/EPUB/ODS/ODP 等格式；CAJ 从"类型无法识别"升级为带建议的显性未解决；修复"内容经派生物提取却报没读到"的核心误报。真实包覆盖率 88.6%→95.1% |
| 2026-08-22 | v0.2.0 | 设计决策 | 确立「资料利用率」为一等目标：降级链（or 语义）之外增加增益轨（and 语义），多视角并行提取；分母靠廉价信号探测而非估计；多轨分歧只记录不裁决；成本靠探测门控/页级增益/身份缓存/条件升级/预算调度五机制，`--profile fast\|balanced\|quality` 是其上的界面。ADR 0002 + spec 11，实现未开始 |
| 2026-08-22 | v0.2.0 | Agent 接口（P5） | 元素级检索索引（SQLite/FTS5，中文逐字切分，新鲜度指纹增量复用）+ `adg index/search/anchor/get`：命中带来源锚点、`quotable` 与未读全提示；查询逻辑单一权威（`gateway.agent_api`）；`adg verify` 20/22 → 25/27；新增 24 项测试 |
| 2026-08-22 | v0.2.0 | 安装体验 | `adg setup` 支持 `--index-url`/`ADG_PIP_INDEX_URL` 国内镜像（不污染全局 pip 配置）；whisper 模型 ModelScope 国内路径指引 + `--worker-option model=` 本地注入；`.gitignore` 忽略 worker 本地模型权重；真实材料包补装 5 个 worker 后覆盖率 34.3%→88.6% |

## 许可证

Apache-2.0。注意：部分 worker 适配器调用的工具是 GPL/AGPL（extract-msg、LibreDWG、MinerU）——它们隔离为独立进程调用，不链入编排器，详见各 `workers/*/worker.json` 的 `license` 字段。
