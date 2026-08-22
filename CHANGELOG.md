# Changelog

> 迭代日志的详情来源。README「迭代日志」章节保存要点，本文件保存每次迭代的完整记录。
> 约定：每次迭代完成后由 Claude 自动追加本文件 + 同步 README「迭代日志」要点 + 推送。

## Unreleased（2026-08-22）

### 安装体验：国内镜像一等公民

- `adg setup` 新增 `--index-url` 与环境变量 `ADG_PIP_INDEX_URL`（优先级：CLI > 环境变量 > pip 默认源），只影响安装动作、不写全局 pip 配置、不涉 worker 沙箱契约；dry-run 与失败提示均展示当前镜像。
- 动机：本机经代理直连 PyPI 出现下载哈希不匹配与 22KB/s 级慢速，清华/阿里镜像实测恢复正常（rapidocr-onnxruntime 全依赖秒级安装）。
- whisper 模型国内路径写入安装指引（setup 提示 + worker.json notes）：huggingface.co 直连慢/易断，建议 ModelScope 下载 `Systran/faster-whisper-small` 后经 `--worker-option model=<本地目录>` 注入；`.gitignore` 新增 `workers/*/models/` 防止模型权重误入库。
- 新增 `tests/test_setup_index.py`（镜像解析优先级 ×4）。

### officecli 实测校准与 fixture 合规化：多轨与 L3 对齐首次真实通过

装上 `officecli` 后第一次真跑（worker.json 原本就写着「输出格式需实测校准」），发现两处问题：

- **适配器从未真正工作过**：没传 `--json`，输出是人类可读文本；解析代码又按 `list` 取结果，
  而实际 JSON 形状是 `{success, data:{matches, results}}`。于是无论文档里有没有批注，
  它都走到"解析不了"分支——**却仍以 `status=ok` 返回，并在 notes 写「批注 0 条；修订 0 条」**。
  这是典型的假装成功：「没有批注」与「我没读到批注」对律师是两回事。
- **验证 fixture 本身不合规**：`make_docx()` 写了 `word/comments.xml`，但既没在
  `[Content_Types].xml` 声明，也没有 `word/_rels/document.xml.rels`。按 OOXML 规范，
  Word 与任何规范实现都看不到这条批注——`docx_lite` 能读到只是因为它直接读 zip 成员、
  绕过关系图。**验证矩阵一直在一个 Word 都打不开的文件上验证"批注可提取"。**

修复：

- 重写 `workers/officecli/adapter.py`（0.1.0 → 0.2.0，maturity experimental → implemented）：
  `query <file> <selector> --json` 三查询（paragraph / comment / revision），
  正文按 `/body/p[N]` 给段落级锚点并识别标题层级；批注取 author/date/done 并经 `anchoredTo`
  挂回段落；修订取 `revision.type`(ins/del)/author/date 并经 `revision.nativePath` 挂回段落。
  **任一查询失败或输出不可解析即 `status=failed`**，交降级链换 parser，绝不给出可能缺轨的产物。
- `make_docx()` 补齐 `[Content_Types].xml` 的 comments/core 声明与 `word/_rels/document.xml.rels`，
  成为合规 OOXML；验证矩阵从此测的是真实场景。

结果：官方 CLI 在合规文档上提出 9 段正文（含标题层级）、1 条批注（作者、锚到第 3 段）、
2 条修订（ins/del、作者、锚到第 4 段）。`adg verify` 26/28 → **27/28、0 失败**——
「L3 跨 parser alignment」首次真实通过（renditions=2, alignments=1）；真实材料包上
docx_lite 与 officecli 双轨对齐 **916 个元素配对、score=1.0**，为 P7 增益轨的交叉校验打好地基。

### DWG 直读与子进程解码加固：覆盖率 95.1% → 97.6%

装上 `libredwg` 后暴露出两个真实问题，都不是"缺工具"能解释的：

- **中文工具输出把 worker 打崩**：所有 adapter 用 `subprocess.run(..., text=True)` 捕获输出，
  即按 UTF-8 解码。中文 CAD 图纸的实体名是 GBK，`dwg2dxf` 往 stderr 一写，
  `UnicodeDecodeError: 0xd0 in position 2934` 直接让整个 worker 崩掉。
  这是**中文材料场景下的通用地雷**——12 个 adapter/模块统一加 `errors="replace"`：
  输出可读、永不抛异常。
- **dwg2dxf 中转不可靠**：转出的 DXF 句柄重复、缺 ENDBLK、`BLOCK_RECORD` 类型错，
  连 `ezdxf.recover`（专为破损 DXF 设计）都拒绝加载，只能靠 dxf_lite 兜底拿到
  `entities_total: 0`——诚实但无用。

修复：新增 `dwgread` worker，用 `dwgread -O JSON` **直读原始 DWG**，绕过 DXF 中转。
实测同一张图纸提出 **9777 条图纸文字**、110 个图层，locator 用 `entity(handle, layer)`
与 dxf_lite/ezdxf 一致。工程纠纷里图纸的证据价值多在标注、图签与说明文字上，这条路径
把"完全读不了"变成"可检索、可回溯到图层与句柄"。`dwg` 链改为
`dwgread → libredwg → dwg_render`（先直读文字，再转 DXF 取几何，最后位图保可视证据）。

内存边界显性化：`dwgread` JSON 对 7MB 图纸膨胀到 86MB，worker 设 `max_json_mb`（默认 512）
上限，超限**显性失败并给出调参指引**，不静默截断也不撑爆内存。

真实包最终：41 items 全量入账，**40/41 可靠读取（97.56%）**，仅剩 CAJ 一项——
知网专有格式无开源解析器，属真实能力边界，带"用 CAJViewer 导出 PDF"的可执行建议。

### 能力补齐与核心误报修复：真实包覆盖率 88.6% → 95.1%

针对真实材料包里剩下的 4 项未解决逐一排查，其中两项根本不是能力缺口而是缺陷：

| 材料 | 原判定 | 真相 / 处置 |
|------|--------|------------|
| `宣传册-未OCR.pdf` | "PDF 加密 → qpdf（需密码）" | **谎报**。该文件是空用户口令的 AES-256（仅所有者限制），任何阅读器都能直接打开。我们从来没试过空口令 |
| `寄快递…-部分OCR.pdf` | "扫描/无文字层 → MinerU（镜像未构建）" | 本机无 Docker/GPU 时扫描件彻底读不了——新增本地位图链 |
| `…研究.caj` | "类型无法识别" | 知网 CAJ（HN 变体），可识别；无开源解析器，应给出可执行建议而不是"不认识" |
| `CAD图纸.dwg` | 缺 `dwg2dxf` | 确属能力缺口，`brew install libredwg` 后可解，指引已正确 |

修复：

- **加密 PDF**：pypdf/pdfplumber adapter 先试给定口令再试空用户口令，成功后在 notes 记录"原件带加密，已用空用户口令解除"（加密事实属证据信息，必须随产物留痕）；路由链 `qpdf` → `pypdf → pdfplumber → qpdf`。真需要口令时才报"需要用户口令"并给出 `--worker-option password=` 指引。
- **扫描 PDF**：新增 `pdf_render` worker（pypdfium2，Apache-2.0，纯 wheel 无系统依赖），把页面渲染为位图交给既有 image 链 OCR；`pdf-scanned` 链变为 `mineru → pdf_render`。实测 6 页扫描件全部 OCR 成功且带 bbox 锚点。
- **CAJ**：magic 表新增 HN/CAJ/KDH 魔数与 `.caj/.kdh/.nh` 扩展名；路由显式 `unresolved` 并给出"用 CAJViewer 导出 PDF 后重新入包"的可执行建议——从"我不认识"升级为"我知道是什么、为什么读不了、你该怎么办"。
- **接入 anydoc**（Firecrawl，Rust，MIT）：14 种办公格式 → 结构化文档模型，无 ML 模型、无系统依赖。实测对比后**只放在它真正更强的位置**：①补齐此前完全无 parser 的 RTF/EPUB/ODS/ODP 及 `.docm/.xlsm/.xlsb/.pptm/.pps/.ppsx/.pot`；②作为 lite parser 之后、LibreOffice/Docling 之前的零依赖第二意见。**不接 PDF**——anydoc 的 PDF 只出 Markdown，拿不到页码锚点，比现有链更弱。合并单元格经 grid 的 origin/span 保留。
- **格式识别**：ODF/EPUB 改按 zip 内 `mimetype` 成员（格式的权威声明）判断，不再靠猜目录名；新增 RTF 魔数。

**核心误报修复（不是本轮新增代码引入，是原有逻辑）**：transform 型原件（扫描 PDF → 页位图、DWG → DXF、视频 → 抽帧、PST → EML）自己没有内容 rendition，内容在派生物里。此前 `reliably_read` 只看自身 rendition，导致 **6 页位图全部 OCR 成功之后，报告仍说这份 PDF "未可靠读取"，并附一条早已不存在的旧错误**。这是"报告说没读到、其实读到了"，比漏读更误导人。修复：新增 `quality.reliably_read_ids()` 做派生闭包判定（支持 PDF → 页位图 → OCR 多级上溯），dispatcher / agent_api / index 统一改用；`_after_transform` 成功后清除该 item 的陈旧 unresolved 条目。

另修：`pdf_render` adapter 起初在 `parse_result` 顶层多放了 `truncated` 字段，被 Worker Protocol 严格录取拦下（schema `additionalProperties: false`）——契约校验按设计工作，字段已移入 `metrics`。

### 缺陷修复：Agent 查询层的六处静默错误答案

本轮自查发现的问题全部位于本次新增的查询层，均已修复并补回归测试：

| # | 问题 | 后果 | 修复 |
|---|------|------|------|
| 1 | `adg list` 直接读 manifest，未走 `agent_api` | 与 spec 10 声称的单一权威不符，且拿不到 `reliably_read` | 改为复用 `list_package_items`，并新增 `--unreliable-only/--limit/--offset` |
| 2 | `--quotable` 与「默认只搜最优 rendition」叠加 | 最优轨恰好不可引用时**谎报"这份材料没有可引用内容"** | `quotable_only` 隐含搜全部 rendition；结果新增 `rendition_scope` |
| 3 | LIKE 降级模式取前 `limit×5` 行再排序 | 真正最佳命中可能落在窗口外且不告知（静默截断） | 候选窗口提到 `max(limit×20, 1000)`，触顶时 `candidate_cap_reached=true` 并在 CLI 提示 |
| 4 | 去重掉的另一份路径在查询层不可见 | "这份合同在证据三和证据五各有一份"这一案卷事实丢失 | `list_package_items`/`get_document` 返回 `duplicate_paths` |
| 5 | `--path-prefix` 只认 `::` 形态 | 用产物目录里的镜像路径（`zip/成员`）过滤会静默返回 0 条，被读成"材料里没有" | 前缀同时按 `::` 与 `/` 两种形态匹配 |
| 6 | `get_document(offset=-1)` 走负数切片 | 返回 0 条却标 `elements_truncated=true`，语义混乱 | offset 夹到非负 |

其中 2、3、5 属同一类：**给出静默的错误答案比报错更危险**——用户会把"没搜到"读成"材料里没有"。

### 设计决策：资料利用率成为一等目标（ADR 0002 + spec 11，实现未开始）

- **问题**：完整性 100% ≠ 信息拿全了。一份 200 页扫描合同文字层提取成功即记完整，
  但公章、手写签名、骑缝章、附件表格全未提取，律师仍要自己翻原件。
- **根因**：`gateway.dispatcher` 的解析链是 `or` 语义——第一个 worker 成功即 `return`，
  链上后续 worker 不再执行。这防住了"锤子坏了没有备用"，没防住"世界不只有钉子"。
- **决策**：利用率与完整性并列为一等目标（正交，不得互相替代）；降级链之外增加
  **增益轨 `enrich`**（`and` 语义，多视角并行，各自成独立 rendition 不合并）；
  利用率分母来自廉价 stdlib **信号探测**（三态 `present`/`absent`/`unknown`，
  不确定必须记 unknown 不得默认 absent）；多轨读数分歧**只记录不裁决**（与「摄取 ≠ 取证」同源）。
- **成本**：降本靠五个机制——探测门控（信号 absent 就不启动 worker）、页级/区域级增益、
  身份缓存 `(source_id, worker, version, config_hash)`、条件升级（低置信度才上第二引擎）、预算调度；
  `--profile fast|balanced|quality` 是其上的用户界面，档位本身不降低成本。
  **快速档也必须报告利用率缺口**——静默跳过增益等同于假装成功。
- 文档：`docs/adr/0002-utilization-as-first-class-goal.md`、`docs/spec/11-utilization.md`；
  README 目标新增第 4 条与状态表一行；`开发计划.md` 路线图新增 P7。

### Agent 接口（P5）：查得到、引得回

- 新增元素级检索索引 `gateway/index.py`：SQLite + FTS5，落在 `<out>/.adg/index/index.sqlite3`。
  - **中文可检索**：FTS5 默认 unicode61 分词器切不开中日韩文本，入库与查询两端共用 `segment_cjk()` 逐字切分，
    因此「担保」能命中「企业对外担保」；两字词不再落空。
  - **新鲜度指纹**（索引版本 | schema 版本 | adg 版本 | manifest.payload_sha256）未变则复用，变了整库重建；
    构建持输出目录单写者锁、先写临时库再原子替换，消费方一律只读打开。
  - FTS5 不可用时降级 LIKE 扫描，`meta.engine` 与每条结果都标注 `engine=like`——不假装成索引。
- 新增 `gateway/agent_api.py`：六个只读查询操作的唯一实现，各查询子命令共用，杜绝同一问题出现两种答案。
  `adg status` 改为复用它（输出与退出码语义不变）。
- 新增 CLI：`adg index`（构建/更新索引）、`adg search`（元素级检索，命中带锚点与 `quotable`）、
  `adg anchor`（element_id 回溯原件位置 + 前后文）、`adg get`（取解析产物、质量八维、fitness、降级链）。
- 定位明确为**给 Agent 用的命令行工具**：Agent 经 Bash 调子命令、`--json` 拿结构化结果，本轮不做 MCP
  （曾实现的只读 MCP server 在同一迭代内按需求撤回，代码保留在 git 历史 `3e696bb`，需要时可取回）。
- 诚实语义贯穿所有查询：检索结果携带 `coverage_caveat`（还有几项没读全，"没搜到"不等于"不存在"），
  每条命中携带 `quotable`（来自 `fitness.quotation`，false = 能检索能作线索、但锚点不足以可靠引用）。
- `adg parse` 结束后自动建索引（`--no-index` 可关），`search/anchor` 开箱即用。
- 新增规格 `docs/spec/10-agent-interface.md`；`gateway/render.py` 新增 `format_locator()` 统一锚点的人类可读形态。

### 契约与恢复边界加固

- Worker Protocol 运行时严格校验：拒绝错误 job ID、未知字段、非 JSONL 和不完整终态；subprocess/Docker 共用真实超时与 stdout+stderr 输出上限。
- Worker 产物统一 containment：只接受 work_dir 内相对普通文件，拒绝绝对路径、`..`、缺失文件和 symlink 逃逸；fragment_path 在录取层安全读取。
- Manifest/EvidenceDocument 读取时执行版本、机器 Schema、payload hash、package/rendition/source 身份校验；缺失引用显式失败。
- 输出目录增加跨进程单写者锁与陈旧写入检测；原子写使用唯一临时文件、fsync 和 replace；下次 parse 自动收养校验通过的孤儿 rendition。
- 修复 force 重跑后质量缓存未失效、derived_from 未持久化/定稿的问题。
- 真实混合材料回归修复 `doc_lite`/`pptx_lite` 的 locator 契约漂移（`line`/`slide` → 规范 `paragraph`，PPTX 页码保留在 `attrs.slide`）。
- 图片元数据兜底在未 OCR 时不再误标为可引用、可做版面分析。
- `--force` 现在能刷新同一 rendition 的最新 run/质量/parser 非身份字段，并追加历史 lineage；普通重跑仍保持幂等复用。
- wheel 现在包含全部 19 个外部 Worker 清单与 adapter；`adg verify` 增加安装产物 Worker 发现守卫。
- 新增声明式总路由表 `gateway.routing.ROUTE_RULES` 和 `adg routes [--kind/--format] [--json]`；实际 Dispatcher 与查询视图共用同一权威，逐条展示识别格式、命中条件、动作、工具链与选择原因，并明确区分 parse / expand / unresolved。

### 验证结果

- `python3 -m unittest discover -s tests`：141 项通过（本迭代累计新增 42 项）。
- 源码 `adg verify`：**27/28 通过、0 失败**，仅 1 项因环境依赖跳过（pypdf 已装，降级用例不适用）；
  新增 5 条 Agent 接口验证项（索引覆盖全部 item、新鲜度指纹避免重复重建、中文两字词命中且带锚点、
  未读全材料显性提示、element_id 回溯到原件）；「扫描件」一项改为按本机能力断言——有 pdf_render 就必须
  渲染成位图并让派生物入账，没有就必须显性失败，两种情形都不允许静默；「降级链失败原因聚合」改用强制
  全不可用链，与本机能力解耦。
- 独立 wheel 安装验证：33 个 Worker 全部可发现，`adg setup --worker pypdf --dry-run` 可定位目标；
  P5 两个模块（index/agent_api）与四个新子命令随 wheel 发布；
  干净 venv 内 `adg verify` 26/27 通过、0 失败，仅 officecli 不可用一项跳过。
- 真实材料包（23 个物理文件，展开/派生后 41 items）：全部入账；**40/41 可靠读取（97.56%）**，
  仅剩 CAJ 一项 unresolved（知网专有格式无开源解析器，属真实能力边界），0 项假成功；
  索引 14123 个元素、FTS5 引擎——加密 PDF、扫描件 OCR、CAD 图纸 9777 条文字均可检索并回溯到锚点。

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
