# 信号探测与多链提取（spec 11）

> 原题《资料利用率与增益轨》。利用率作为比值已由 ADR 0004 撤销，触发机制已由 ADR 0005 取代。

> 状态：**实现中（一期：信号探测）**。信号探测已落地——`gateway/signals.py` 持有信号清单，
> 探测随 `adg scan` 执行并写入 manifest item 的 `signals` 字段（Schema v0.2）。
> **目前只有 PDF 有探测器，其余 kind 的信号一律 unknown。**
> §4 多链并跑与 §6.2 三档 profile 已实现（`adg parse --profile`，执行记录落 `item.chain_executions`）；
> §5 分歧记录、§6.1 的页级增益/身份缓存/条件升级/预算调度尚未实现。
>
> ⚠️ **原定的利用率比值已由 [ADR 0004](../adr/0004-utilization-is-not-a-ratio.md) 撤销。**
> 分子（已成功提取的信号数）需要 ground truth，而有 ground truth 就不需要这些工具——
> 原理上不可知。改为输出「探测到什么 + 跑过什么」两组事实，不做除法、不合成评分。
> 本文 §3.3 与 §一表格已按此改写；§4 增益轨等其余设计不受影响。
> 任何"已支持"的说法以 `adg` 实际行为与验证矩阵为准。

## 一、两个正交维度

摄取质量有两个互相独立的问题，此前项目只回答了第一个：

| 维度 | 问的问题 | 方向 | 输出 | 现状 |
|------|---------|------|------|------|
| **完整性** completeness | 这一包 35 个文件，都处理了吗？ | 横向 / 文件级 | `quality.extraction_coverage`（比值） | 已实现 |
| **信号覆盖** signal coverage | 这一份合同里有哪些种类的信息？各跑过什么工具？ | 纵向 / 文件内 | `item.signals` 三态 + 执行记录（**不是比值**） | 探测已实现 |

两者不能互相替代。一份 200 页扫描合同，文字层提取成功 → 完整性记 100%，
但公章位置、手写签名、骑缝章、附件表格是否提到，完整性一个字都不会说。
**只报完整性会让"没读到"伪装成"读完了"**，这与项目「不假装成功」的核心承诺冲突。

第二个维度的输出形态是**事实**而非分数：探测到这份材料有 `page_images`、
实际跑过 `pdf_render → rapidocr`。够不够、要不要再跑一个，由调用方判断。
「提出来了多少」无法计算——见 [ADR 0004](../adr/0004-utilization-is-not-a-ratio.md)。

## 二、为什么现在做不到：降级链是 `or` 语义

`gateway.dispatcher` 的解析链在第一个 worker 成功后即 `return`
（[dispatcher.py](../../gateway/dispatcher.py) `parse 成功 → 内容 rendition 已得，结束`）。
因此 pypdf 成功之后，rapidocr 永远不会去看同一份 PDF 里的印章。

- **降级链 fallback**：为了拿到"能用的结果"——`or` 语义，先成功先得。**这是备胎，不是多元视角。**
- **增益轨 enrichment**：为了拿到"更全的结果"——`and` 语义，多轨并行后各自成 rendition。

不同 worker 看到的是**不同种类的事实**，不是同一事实的不同精度：

```
一份数字 PDF
├─ pdfplumber   文字层 + 表格结构 + 坐标        ← 文字视角（主轨）
├─ rapidocr     图片区域内的字、印章、手写        ← 视觉视角（文字层永远看不到）
├─ pypdf        元数据、书签、附件、表单域        ← 容器视角
└─ pyhanko/qpdf 电子签章技术信号                 ← 密码学视角（仍恒不作真伪判断）
```

## 三、信号清单：探测什么

**探测结果必须是事实，不能是估计或经验值**——这是本规范的底线，与
[spec 03](03-package-manifest.md) 中「完整入账靠清点而非感觉」同一纪律。

### 3.1 各 kind 的可提取信号（初版，随实现修订）

| kind | 信号类型 |
|------|---------|
| pdf | `text_layer` `tables` `page_images` `annotations` `form_fields` `attachments` `bookmarks` `metadata` `signature_signals` |
| docx | `body_text` `tables` `comments` `revisions` `headers_footers` `footnotes` `embedded_objects` `images` `metadata` |
| xlsx / xls | `cells` `formulas` `extra_sheets` `cell_comments` `charts` `hidden_content` `metadata` |
| pptx / ppt | `slide_text` `speaker_notes` `tables` `slide_images` `metadata` |
| image | `ocr_text` `exif` `seal_regions` |
| audio / video | `transcript` `speaker_turns` `frame_text` `metadata` |
| email | `body` `headers` `attachments` `inline_images` |
| dwg / dxf | `entities` `layers` `text_labels` `thumbnail` |

### 3.2 探测规则（probe）

1. **廉价**：探测必须由编排器用 stdlib 完成（解 PDF 对象树、解 zip 读 XML、读文件头），
   不得为了探测去启动重型 worker。
2. **三态**：每个信号取 `present` / `absent` / `unknown`。探测器不确定时必须记 `unknown`，
   **不得默认 absent**——把「我不知道」偷换成「它不存在」，是本规范最不能容忍的一种失真。
3. **落盘**：探测结果写入 manifest item 的 `signals` 字段，与 `detected_type` 同级，供路由与报告共用。
4. **探测结果 = 调度过滤器**：同一份探测既是给调用方的事实，也是链的门控条件——
   探测到 0 张图片就不必启动 OCR。这是成本控制的主要来源（见第六节）。

### 3.3 输出形态：事实，不是比值

每个 item 输出两组并列的事实，互不合成：

```
signals    {text_layer: absent, page_images: present, tables: unknown, ...}
执行记录    实际调用了哪些工具、产出了哪些 rendition
```

- 三态如实呈现。把 `unknown` 折叠进 `absent` 就是在假装知道，把 `absent` 说成"已提取"
  更是。`unknown` 要单独可数，报告中列出（"3 项信号无法探测"）。
- **不做除法。** 分子「已成功提取的信号数」需要 ground truth，而 ADG 若掌握 ground truth
  就不必调用这些工具——原理上不可知，见 [ADR 0004](../adr/0004-utilization-is-not-a-ratio.md)。
  任何形式的利用率百分比、包级加权利用率、信号权重表，一律不做。
- **不合成评分。** 多个工具产出多个 rendition，哪个好、够不够、要不要再跑一个，
  ADG 不告诉调用方，判断权在调用方（ADR 0004 决策 4）。
- 信号覆盖**独立于八维质量**：八维答"这份产物提得好不好"，信号覆盖答"有哪些种类的信息、
  各碰过什么工具"，正交。

## 四、增益轨

> ⚠️ **触发机制已由 [ADR 0005](../adr/0005-workflow-selection-by-profile.md) 取代。**
> 下文原定的第四种 action `enrich` 与「信号缺口驱动」不再采用：缺口驱动需要一张
> 「信号 ↔ worker 能力」映射，而 [ADR 0004](../adr/0004-utilization-is-not-a-ratio.md) 决策 5
> 已否决它。现行设计是 `parse` 动作按 `--profile` 跑 N 条链，`action` 取值集合不变。
> 规则 2、3、4 不受影响。

`gateway.routing.ROUTE_RULES` 的 `parse` 动作从「跑一条链到成功」扩展为
「按 profile 跑 N 条链」：链**内**仍是 `or`（降级纪律见 spec 08），链**之间**是可选的 `and`。

规则：

1. ~~触发条件 = 信号 present 且主轨未覆盖该信号~~ → **改为：跑几条链由 `--profile` 决定**
   （fast 只跑首选链，quality 跑全部可用链）。信号探测仍是**路由输入与探测门控**
   ——探测到没有图就不必启动 OCR，这是成本控制的主要来源，见 §6.1。
2. **粒度尽可能细**：页级 / 区域级优先于文件级。例：只对"含 image XObject 且文字层未覆盖"的页做 OCR，
   而不是整份 200 页 PDF 重跑。
3. **产物是独立 rendition，不合并**。多个 rendition 各自保留身份与 lineage，
   检索层做 union（`adg search --all-renditions`）。
   **理由**：合并会破坏 `rendition_id` 的内容哈希语义与 lineage 的可追溯性，
   这是项目最值钱的两个不变量（见 [ADR 0001](../adr/0001-contract-authority-and-write-ownership.md) 第 6 条）。
4. **并跑失败不影响主轨结论**：主轨已成功的 item 不因其他链失败而降级；未执行或失败的链
   必须进**执行记录**（ADR 0005 决策 5），不写入 `parser.fell_back_from`——那是链内回退的记录。

## 五、分歧记录：多元模型的第二层价值

多轨提取**同一**信号时会出现读数不一致。分歧本身是高价值信号——
两个 parser 对同一处金额给出不同结果，这一条对律师的价值可能高于其他 99 条读对的内容。

1. **复用现有基础设施**：`cross_rendition_alignment` 与 `alignment_score`
   （[spec 07](07-source-anchors.md)）目前只用于 L3 身份对齐，扩展为分歧检测的输入。
2. **只记录，不裁决**。摄取层不得自动选出"正确"读数——这与
   「摄取 ≠ 取证」同源：系统提供线索，判断权归人。
3. **分级呈现**：金额、日期、当事人名称、案号的分歧优先告警；正文措辞级差异降噪，
   否则工具越多分歧越多，律师会被淹没。
4. 输出形态（示意）：

```
⚠️ 合同.pdf 第 12 页：两个 parser 读数不一致，需人工核对
   pdfplumber(文字层): "贰佰万元"    rapidocr(视觉): "贰百万元"
```

## 六、成本控制

增益轨天然更贵。**档位本身不降低成本，它只让用户选择花多少**；真正降低成本的是下列机制，
档位是它们之上的用户界面。

### 6.1 五个降本机制

| 机制 | 做法 | 预期效果 |
|------|------|---------|
| **探测门控** | 信号 absent 就不启动对应 worker | 最大一笔节省：探测是毫秒级，OCR 是秒/分钟级 |
| **页级 / 区域级增益** | 只处理未被主轨覆盖的页或 bbox 区域 | 典型文档可降一个数量级 |
| **身份缓存** | `(source_id, worker, version, config_hash)` 命中即跳过 | 重跑成本趋近 0（`source_id` 已是内容哈希，地基现成） |
| **条件升级** | 只在低置信度或结构异常处升级第二引擎（如 OCR 置信度 < 阈值） | 比固定档位更省，且更准 |
| **预算调度** | 按"预期收益/成本"排序增益任务，跑到预算耗尽 | 上限可控 |

**预算耗尽必须显性化**：未执行的链记入执行记录并出现在报告中，
不得静默跳过——静默跳过就是假装读完了。

### 6.2 三档 profile

`adg parse --profile fast|balanced|quality`（默认 `balanced`）：

| 档 | 行为 | 成本 | 适用 |
|----|------|------|------|
| **快速** fast | 主轨跳过重工具（heavy / docker），用链上轻的那些；不并跑。**仍执行信号探测并如实呈现三态** | 低 | 先摸清一包材料的规模 |
| **均衡** balanced | 主轨走整条链、先成功先得，拿到效果最好的可用工具；不并跑 | 中 | 默认 |
| **质量** quality | 主轨整条链 + 并跑声明的其他链，多个工作流各自出结果；后续加交叉校验与分歧检测 | 高 | 重点材料、开庭前核对 |

三档选的是**工作流**而不只是数量：`fast` 与 `balanced` 的区别在主轨用什么工具——
若 `fast` 也走整条链，它照样先试最重的工具，就一点都不快（[ADR 0005](../adr/0005-workflow-selection-by-profile.md) 决策 3）。

两条不变量：

1. **快速档也必须如实报告信号三态与执行记录**。"快速"的含义是"我告诉你探测到什么、跑了什么，但你让我先跑快的"，
   **不是**"我假装提完了"。这是本规范与「不假装成功」承诺的接口。
2. **档位可按路径/kind 覆盖**：`adg parse --profile fast --focus 证据三/ --focus-profile quality`
   —— 重点材料吃质量档，其余吃快速档。这是最省钱的实际用法。

## 七、验证（实现时补入验证矩阵）

| 判据 | 方法 | 状态 |
|------|------|------|
| 探测结果是事实 | 构造含 N 张图 / M 处批注的 fixture，探测结果必须与手工清点一致 | 一期已验 |
| 探测不确定不虚报 | 构造探测器读不了的样本，信号须记 `unknown`，不得判 `absent` | 一期已验（含变异测试） |
| 不出现比值 | 输出中不得含 `utilization` 或任何信号百分比（ADR 0004） | 一期已验 |
| 增益轨不覆盖主轨 | 主轨成功后增益轨产物必须是新 rendition，主轨 rendition_id 不变 | 待二期 |
| 探测门控真的省 | 无图 PDF 必须零次 OCR 调用 | 待二期 |
| 快速档诚实 | `--profile fast` 下信号三态与执行记录仍完整出现在报告中 | 待三期 |
| 预算耗尽显性 | 人为设小预算，未执行的增益任务必须出现在执行记录中而非消失 | 待三期 |
| 分歧不自动裁决 | 构造两轨读数冲突样本，产物须同时保留两个读数并标记需人工核对 | 待四期 |
