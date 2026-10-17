# Changelog

> 迭代日志的详情来源。README「迭代日志」章节保存要点，本文件保存每次迭代的完整记录。
> 约定：每次迭代完成后由 Claude 自动追加本文件 + 同步 README「迭代日志」要点 + 推送。

## Unreleased

### 2026-10-17：官网与开源发布

- 新增 `site/` 交互官网、80 秒网络版宣传片、团队头像与海报，补充 `docs/assets/readme/` 配图。
- 官网许可文案与用户已更新的 MIT `LICENSE`、项目元数据保持一致；README 使用正式主页 `https://adg.chenshi.ai/`。
- 发布采用 Cloudflare Pages + GitHub `main` 自动部署，输出目录为 `site/`，无构建步骤；仅网站目录进入 CDN，本地输入、工作底稿、规则与视频母版不上传。
- 官网扫描 PDF 示例校准为 `quality` 档并跑 VLM；复制命令排除需替换 `<element_id>` 的示例，避免直接粘贴引发 shell 重定向语法错误。
- 为主页补充 canonical 与 Open Graph 分享元数据；项目简介、主页与主题标签同步至 GitHub。

### 许可证变更为 MIT

- 项目许可证由 Apache-2.0 改为 MIT：仓库根目录新增 `LICENSE`（Copyright (c) 2026 ADG Contributors），`pyproject.toml` 的 `license` 改为 `MIT` 并声明 `license-files`，README 徽章与许可证章节同步。GPL/AGPL 等第三方工具仍以独立进程隔离调用，各自许可以 `workers/*/worker.json` 的 `license` 字段为准。

### v0.2.4：锚点唯一性与安全拦截显性化

- element_id 派生加入 source_id（spec 07 同步）：此前同 parser/同 locator 的不同原件共享同一
  element_id——真实材料包 14419 个元素仅 13051 个唯一 ID，最坏一个 ID 横跨 15 个源文件，
  `adg anchor` 回溯会列出无关原件。现在锚点唯一指回原件；已有产物需 `adg parse --force` 重跑后生效。
- 压缩包成员安全拦截显性化：有成员被安全拦截的容器不再计入「可靠读取」（覆盖率随之诚实化），拦截
  明细（成员名+原因）进入容器 item.errors、`adg status`（新增 `security_interceptions` 字段与终端
  ⛔ 明细）、manifest.md 与 report.md 的「压缩包成员安全拦截」小节——此前仅存在于 manifest.json
  的 integrity 里，spec 06 要求的「拦截显性记录」在各展示层均不可见。
- 「哪些没读全」口径统一：包级排除（如符号链接不入包）此前只在 report 出现、status 缺失，同一问题
  两种答案；现在 `adg status` 的 not_reliably_read 与 report「未可靠读取清单」同口径（item 级不可靠
  + 包级排除），report 原「未可靠读取与待办」更名「未解决与排除明细」以消歧。
- 小修复：`.ost` 魔数识别恒判为 pst（email-ost 路由规则成为死规则）；docx_lite 存在未展开嵌入对象时
  attachments 维度恒 pass；csv_lite locator 死表达式清理。
- 验证：源码 246 项测试通过；`adg verify` 27/28（1 项环境跳过）；对抗包实测（路径穿越/绝对路径/
  zip bomb/符号链接/FTS5 注入）拦截记录全链路可见，anchor 回溯唯一命中。

### v0.2.3：真实安装与 OCR 质量修复

- 修复 m1max 独立安装实测暴露的运行问题：PaddleOCR 3.x API/结果结构兼容；重型依赖用模块发现代替冷导入
  探测；OCR/VLM 第三方日志从 JSONL stdout 隔离到 stderr；OvisOCR2 补 `torchvision`、改传本地路径，并在
  生成期提前停止、清理尾部重复循环。
- 收紧质量诚实性：PaddleOCR-VL 用图片占位符代替正文、确定性 OCR 多数文本框低置信、内容截断时，不再
  声明可事实提取或可引用；OvisOCR2 重复循环结果强制标记为不完整并继续链内降级；包级可靠覆盖率为 0
  时不再误报 `overall=pass`。
- 发布版本升至 0.2.3；源码 245 项测试通过；中文法律长图 `quality` 模式在 m1max 上完成真实模型回归，
  PaddleOCR 形成可靠主结果，PaddleOCR-VL/OvisOCR2 的不完整结果均被正确降级。

### v0.2.2：文档 VLM 与承重契约修复

- 新增 PaddleOCR-VL-1.6、OvisOCR2 Worker；真实模型 fixture 尚未验收，成熟度保持
  `protocol-smoke`，默认 balanced 主链继续使用已验证工具，quality/显式 `--chain` 才执行新模型。
- rendition 在任何持久化之前执行完整 EvidenceDocument Schema、哈希与身份校验；无效 worker
  结果按 `rejected_by_preflight` 继续降级，不再留下损坏孤儿文件。
- 链内 `status=ok` 不再等于内容可用：未达到 `reliably_read` 下限时记录 `low_quality` 并继续；
  整链均低质量时仍保留末级降级证据，但不会计为可靠读取。
- Worker 可用性统一合并运行时存在性与硬件要求，Dispatcher、check、doctor/setup 共用同一结论；
  `hardware_unmet` 进入真实失败记录。
- subprocess 明确限定为受信任代码的弱隔离，并在代码层拒绝 `network=true` worker；未知或恶意
  worker 必须使用提供网络/文件系统强制策略的容器。
- lineage 可记录实际顶层依赖版本；文档 VLM 记录模型 revision 与推理配置哈希，Ovis 达到生成
  token 上限时显式标记截断并触发降级。
- 发布版本升至 0.2.2；setuptools 排除陈旧 build/dist 包树，CI 从干净源码构建 wheel 并校验
  新增/已删除 Worker 的实际包内容。
- 验证：源码 229 项测试通过；`adg verify` 27/28、0 失败、1 项按环境跳过；临时干净源码构建的
  0.2.2 wheel 可独立安装，发现 PaddleOCR-VL/OvisOCR2，且不含 GLM-OCR/HPD 残留。

### 安装简化：`adg setup --auto` 与自动线路优化

面向不熟悉命令行的目标用户（律师、文字工作者），把安装从"先搞懂 --lite/--all 的区别、
再自己找镜像、再手动下模型"压成一条命令。

- **新增 `gateway/mirrors.py`**：并发测速 pypi 官方/清华/阿里/腾讯/中科大，选最快的一个，
  结果按 7 天 TTL 缓存到 `~/.cache/adg/mirrors.json`。
  - 单次测速抖动可达 2 倍，因此每个候选测 2 次取最小值——**实测中这一条救了一次误判**：
    hf-mirror 首探握手超时，二探正常（3.3s），单次探测会错误地告诉用户"模型线路全不通"。
  - 优先级：`--index-url` > `ADG_PIP_INDEX_URL` > 自动测速 > pip 默认。显式指定永远压过自动。
  - 官方最快时返回 `None` 用 pip 自带默认源，不硬塞镜像 URL。
  - 全部候选不可达时 `Route.reachable=False` 并显性告警——本机实测 pypi 官方直连 SSL 握手失败，
    自动测速正好绕开了这个坑。
- **新增 `adg setup --auto`**：装齐"免系统依赖"的 worker（本机 10 个），跳过需 Docker
  或数 GB 模型的（docling/mineru/paddleocr/whisper），结尾打印「现在能读什么、还差什么、
  缺的怎么补」。准入声明在各 `worker.json` 的 `auto_install` 字段，**不是代码里的硬编码名单**
  ——与路由表同样的纪律：能力事实归清单，代码只执行规则。
- **模型预热**：whisper/paddleocr 的 `worker.json` 新增 `prefetch` 声明（命令 + 体积）。
  这些 worker 声明 `network: false`，但模型是首次运行才下载的——**不预热的话，第一次解析音视频
  会在一个声称不联网的 worker 里静默等待下载**。预热把下载提到安装阶段，让那句声明成真。
  下载经自动选出的 `HF_ENDPOINT` 走镜像；线路全不通时直接给出 ModelScope 手动路径而不是干等。
- 模型下载在**安装阶段**完成，减少解析阶段的意外联网；`env_passthrough` 白名单保持不变。
  这不把 subprocess 冒充为网络沙箱——需要强制断网时仍必须使用容器后端。
- 新增 `tests/test_mirrors.py`（12 项，全部 mock 探测，测试套件不联网）。

**未解决的门槛（已写入 README）**：仍需本机有 Python 3.10+ 且会开终端。
单文件可执行（免 Python）与图形界面尚未提供——对完全不碰命令行的用户，这才是真正的墙。

### 架构审计（keel）：4 处载重缺陷

对 v0.2.0 做载重面审计，按仓库自己声明的优先级取证（ADR 0001：`models/*.json` 为结构契约规范源，
`docs/spec/` 定语义与不变量，README 只是投影）。发现并修复 4 处，每处都补了可证伪守卫：

| # | 缺陷 | 证据 | 修复 |
|---|------|------|------|
| 1 | **质量维度对 doc/xls/dwg/presentation 不压级**：`expected_for` 遗漏这些 kind，按 spec 05 的语义等于声明"它们不需要提取文本"。同一份「文本提取失败 + 锚定失败」的建议维度，pdf/docx/xlsx 定稿为 `fail`，而 **doc/xls/dwg 定稿为 `pass`** | 代码实证：`aggregate_overall` 对 doc/xls/dwg 返回 pass | 补齐三个内容维度的 expected_for；新增守卫「凡有 parse 链的 kind，text_extraction 与 source_anchoring 必须在 expected_for 内」+ 负向对照 |
| 2 | **可靠读取判定有三处绕过包级权威**：`adg report`、dispatcher 渲染索引、以及渲染索引里「无 rendition 即 reliable=False」的硬编码 | `adg status` 说 40、`adg report` 说 **39**，同一个包两个答案 | 三处统一改用 `quality.reliably_read_ids`；新增结构守卫：除 `gateway/quality.py` 外任何模块出现 `reliably_read_item(` 即失败 |
| 3 | **Agent 消费的 `--json` 输出无契约版本**：下游无从判定契约是否变过；且原有 `API_VERSION = __version__` 把契约绑在发布版本上，等于没有版本 | status/list/search/get 四个 JSON 均无版本字段 | 新增独立的 `AGENT_API_VERSION`（当前 `"1"`），六个操作统一盖戳；测试断言它**不等于**发布版本 |
| 4 | **索引需重建但输出目录被占用时查询整个失败**：parse 期间所有检索不可用，而此时往往已有一份可用的旧索引 | 持锁重建实证抛 `ConcurrentWriteError` | 降级用既有索引并置 `index.stale=true` + `stale_index_caveat`，CLI 同步提示；既有索引都没有时仍显性失败——那时无话可说 |

关于 #1 的契约定性：这是 ADR 0001 第 5 条意义上的**一致性修正**（实现早已产出这些 kind 的 rendition，
规则文件遗漏了它们），不是破坏性变更——持久化结构未变，`schema_version` 保持 `0.1`。
影响是未来定稿更严格：真实包里 `.doc` 的 overall 从 `pass` 降为 `pass_with_warnings`
（doc_lite 确实没有版面保留），覆盖率不变（`reliably_read` 走 fitness 而非 overall）。

### 验证结果

- `python3 -m unittest discover -s tests`：**163 项通过**（新增 22 项：质量覆盖守卫 3、单一权威守卫 2、
  契约版本 2、陈旧索引降级 3、线路优化 12）。
- `adg verify`：27/28、0 失败。
- 真实材料包：41 items 全量入账、40/41 可靠读取（97.56%）不变；`adg report` 与 `adg status` 一致。

## v0.2.0（2026-08-23）

本版把摄取产物接到 Agent 手上（检索 + 回溯），把真实材料包的覆盖率从 79.4% 推到 97.6%，
并修掉一批"看起来在工作、其实没在工作"的缺陷。要点：

- **Agent 接口（P5）**：元素级 SQLite/FTS5 检索索引（中文逐字切分）+ `adg index/search/anchor/get`，
  命中带来源锚点、`quotable` 与未读全提示；查询逻辑单一权威（`gateway.agent_api`）。
- **能力补齐**：加密 PDF 空口令、扫描 PDF 本地位图 OCR 链（`pdf_render`）、DWG 直读（`dwgread`）、
  anydoc 接入（RTF/EPUB/ODS/ODP 等）、CAJ 识别。真实包 40/41 可靠读取（97.56%）。
- **诚实性缺陷修复**：12 处"静默错误答案"或"假装成功"，其中 3 处是核心级——
  内容经派生物提取却报没读到、officecli 读不到批注却报「0 条」、验证 fixture 本身不合规。
- **设计定稿**：确立「资料利用率」为一等目标（ADR 0002 + spec 11），实现留待 P7。


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
