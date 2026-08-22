# Changelog

> 迭代日志的详情来源。README「迭代日志」章节保存要点，本文件保存每次迭代的完整记录。
> 约定：每次迭代完成后由 Claude 自动追加本文件 + 同步 README「迭代日志」要点 + 推送。

## Unreleased（2026-08-22）

### 安装体验：国内镜像一等公民

- `adg setup` 新增 `--index-url` 与环境变量 `ADG_PIP_INDEX_URL`（优先级：CLI > 环境变量 > pip 默认源），只影响安装动作、不写全局 pip 配置、不涉 worker 沙箱契约；dry-run 与失败提示均展示当前镜像。
- 动机：本机经代理直连 PyPI 出现下载哈希不匹配与 22KB/s 级慢速，清华/阿里镜像实测恢复正常（rapidocr-onnxruntime 全依赖秒级安装）。
- whisper 模型国内路径写入安装指引（setup 提示 + worker.json notes）：huggingface.co 直连慢/易断，建议 ModelScope 下载 `Systran/faster-whisper-small` 后经 `--worker-option model=<本地目录>` 注入；`.gitignore` 新增 `workers/*/models/` 防止模型权重误入库。
- 新增 `tests/test_setup_index.py`（镜像解析优先级 ×4）。

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

- `python3 -m unittest discover -s tests`：130 项通过（新增 31 项覆盖切分/索引/检索/回溯/六个查询操作，含上表六处缺陷的回归用例）。
- 源码 `adg verify`：25/27 通过、0 失败、2 项因环境依赖跳过（officecli 不可用、pypdf 已装故降级用例跳过）；
  新增 5 条验证项：索引覆盖全部 item、新鲜度指纹避免重复重建、中文两字词命中且带锚点、未读全材料显性提示、
  element_id 回溯到原件。
- 独立 wheel 安装验证：33 个 Worker 全部可发现，`adg setup --worker pypdf --dry-run` 可定位目标；
  P5 两个模块（index/agent_api）与四个新子命令随 wheel 发布；
  干净 venv 内 `adg verify` 26/27 通过、0 失败，仅 officecli 不可用一项跳过。
- 真实材料包（23 个物理文件，展开/派生后 35 items）：全部入账；补装 worker 后 31/35 可靠读取（88.57%），
  4 项 unresolved（CAJ 类型未识别、DWG 缺 dwg2dxf、加密 PDF 缺 qpdf、扫描 PDF 缺 MinerU 镜像）全部带原因与补装建议，0 项假成功；
  索引 3100 个元素、FTS5 引擎，中文检索与锚点回溯在真实材料上实测通过。

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
