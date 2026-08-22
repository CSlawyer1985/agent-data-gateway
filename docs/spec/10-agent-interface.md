# Agent 接口（spec 10）

> 摄取产物只有被 Agent 查得到、引得回，才算完成闭环。本规范定义检索索引、六个只读查询操作，
> 以及 Agent 经 Bash 调用 `adg` 时必须得到的诚实语义。

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

## 六个只读查询操作

Agent 经 Bash 调 `adg` 子命令；每个子命令加 `--json` 输出可直接消费的结构化结果。

| CLI | agent_api 操作 | 回答的问题 |
|-----|---------------|-----------|
| `adg status` | `get_ingestion_status` / `list_unresolved_items` | 材料读全了吗？哪些没读全、为什么、怎么补？ |
| `adg list` | `list_package_items` | 包里都有什么？（含展开出的成员） |
| `adg search` | `search_elements` | 哪份材料、哪个位置提到了 X？ |
| `adg get` | `get_document` | 这份材料解析成了什么？质量与降级链如何？ |
| `adg anchor` | `resolve_anchor` | 这条引用出自原件哪里？能不能可靠引用？ |

### 检索结果契约

每条命中至少包含：`element_id`、`rendition_id`、`source_id`、`package_relative_path`、`kind`、
`locator`、`snippet`、`parser`、`quality`、`quotable`、`engine`。

- `quotable` 直接取自该 rendition 的 `fitness.quotation`：**false 表示这段内容能检索、能作线索，
  但不能作为可靠引用**（例如 pdflite 兜底产物的锚点是近似的）。
- 默认只搜每个文件的最优 rendition；`all_renditions` 打开后可跨 rendition 比对。

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
