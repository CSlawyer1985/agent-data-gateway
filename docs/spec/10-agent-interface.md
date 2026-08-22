# Agent 接口（spec 10）

> 摄取产物只有被 Agent 查得到、引得回，才算完成闭环。本规范定义检索索引、六个只读查询操作，
> 以及 Agent 经 Bash 调用 `adg` 时必须得到的诚实语义。

## 契约版本

六个操作的返回值一律携带 `contract_version`（当前 `"1"`，见 `gateway.agent_api.AGENT_API_VERSION`）。

- **它独立于 `adg` 发布版本**：发版不改查询契约就不动它，字段语义变了才递增。
  绑在发布版本上等于没有版本——下游无法据此判定契约是否变过。
- 消费方应按 `contract_version` 决定解析策略；本层不承诺跨大版本的字段稳定性。

## 边界

- **只读**：本层永不修改 manifest / rendition / 原件。唯一写动作是重建检索索引——索引是可丢弃的派生物。
- **单一权威**：所有查询子命令委托同一个 `gateway.agent_api.PackageView`，同一问题只有一个答案。
- **检索范围 = 已可靠读取的部分**：未解析、解析失败、未解决的材料不在索引内。任何检索结果都必须携带
  这一事实（`coverage_caveat`），否则"没搜到"会被误读为"不存在"。

## 检索索引

位置 `<out>/.adg/index/index.sqlite3`（内部产物，可随时删除重建）。

| 表 | 内容 |
|----|------|
| `meta` | 索引结构版本、引擎、新鲜度指纹、包身份与计数 |
| `items` | manifest 条目 + `reliably_read` 判定 |
| `renditions` | parser、质量 overall、fitness 四旗标、元素数、是否最优 rendition |
| `elements` | 元素级：element_id、locator（JSON）、原文、所属块 |
| `elements_fts` | FTS5 全文索引（切分后文本） |

规则：

1. **新鲜度指纹** = `索引结构版本 | schema 版本 | adg 版本 | manifest.payload_sha256`。指纹未变直接复用，
   变了整库重建——索引不做增量合并，避免与产物出现无法诊断的偏差。
2. **构建持输出目录单写者锁**，先写临时库再原子替换；消费方一律 `mode=ro` 只读打开。
3. **中日韩逐字切分**：入库与查询共用 `segment_cjk()`，FTS5 默认 unicode61 分词器切不开中文，
   不切分则「担保」永远搜不到「企业对外担保」。查询端把每个词编成短语（`"担 保"*`）。
4. **FTS5 不可用时降级 LIKE 扫描**，`meta.engine` 与每条检索结果都标注 `engine=like`——不假装成索引。
5. **超长元素**（>20000 字符）入库截断并计数，原文仍以 rendition 为准。

## 去重与可见性

同字节文件只保留一个 item（`dedup_saved`），但**另几份路径是案卷事实，不得就此消失**：
`list_package_items` 与 `get_document` 必须返回 `duplicate_paths`（来自 `quality.duplicates`）。
"这份合同在证据三和证据五各有一份"对律师有独立意义。

## 六个只读查询操作

Agent 经 Bash 调 `adg` 子命令；每个子命令加 `--json` 输出可直接消费的结构化结果。

| CLI | agent_api 操作 | 回答的问题 |
|-----|---------------|-----------|
| `adg status` | `get_ingestion_status` / `list_unresolved_items` | 材料读全了吗？哪些没读全、为什么、怎么补？ |
| `adg list` | `list_package_items` | 包里都有什么？（含展开出的成员与去重掉的重复路径） |
| `adg search` | `search_elements` | 哪份材料、哪个位置提到了 X？ |
| `adg get` | `get_document` | 这份材料解析成了什么？质量与降级链如何？ |
| `adg anchor` | `resolve_anchor` | 这条引用出自原件哪里？能不能可靠引用？ |

### 检索结果契约

每条命中至少包含：`element_id`、`rendition_id`、`source_id`、`package_relative_path`、`kind`、
`locator`、`snippet`、`parser`、`quality`、`quotable`、`engine`。

- `quotable` 直接取自该 rendition 的 `fitness.quotation`：**false 表示这段内容能检索、能作线索，
  但不能作为可靠引用**（例如 pdflite 兜底产物的锚点是近似的）。
- 默认只搜每个文件的最优 rendition（`rendition_scope=best_rendition`）；`all_renditions` 打开后跨 rendition 比对。
- **`quotable_only` 隐含 `all_renditions`**：用户要的是"能引用的内容"，若只搜最优 rendition，
  最优轨恰好不可引用时会谎报"这份材料没有可引用内容"。
- `path_prefix` 同时按包内原始形态（容器成员用 `::`）与镜像形态（用 `/`）匹配——
  用户从产物目录抄来的路径是镜像形态，不得因此静默返回 0 条。
- **无静默截断**：LIKE 降级模式没有 bm25，只能在候选窗口内排序；窗口触顶时
  `candidate_cap_reached=true`，调用方与 CLI 都必须显性提示结果可能非全局最佳。
- **陈旧索引降级**：索引需要重建、但输出目录正被 parse 之类的写入者占用时，
  若已有一份可读索引就用它并置 `index.stale=true` 与 `stale_index_caveat`——
  查询照常可用，但调用方知道结果可能缺最新解析产物。既有索引都没有时才失败：
  那时无话可说，假装查过比失败更糟。

### 退出码（CLI 入口）

沿用全局三态语义：`0`=pass / `1`=pass_with_warnings / `2`=fail。本层的映射：

| 命令 | 1 的含义 |
|------|---------|
| `adg search` | 未命中——**不等于"材料里没有"**，先看 `adg status` 是否还有没读全的材料 |
| `adg anchor` | element_id 未找到（索引过期时用 `adg index --force` 重建） |
| `adg get` | 该材料尚无解析产物 |
| `adg status` | 存在未可靠读取的材料 |

### 回溯契约

`resolve_anchor(element_id)` 返回全部命中的 rendition（同一 element_id 可能存在于多个 rendition），
每条含原件路径、parser 与版本、locator、前后文、`quotable`。定位器语义见 [spec 07](07-source-anchors.md)。

## 验证

验证矩阵（`adg verify`）含本层五项：索引覆盖全部 item、新鲜度指纹避免重复重建、
中文两字词命中且带锚点、未读全材料显性提示、element_id 回溯到原件。
隐私项同时校验只读接口返回值不含绝对路径。
