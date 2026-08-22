# 资料利用率与增益轨（spec 11）

> 状态：**设计中，尚未实现**。本文定义概念、度量与不变量，供实现与评审对齐；
> 任何"已支持"的说法以 `adg` 实际行为与验证矩阵为准。

## 一、两个正交维度

摄取质量有两个互相独立的问题，此前项目只回答了第一个：

| 维度 | 问的问题 | 方向 | 度量 | 现状 |
|------|---------|------|------|------|
| **完整性** completeness | 这一包 35 个文件，都处理了吗？ | 横向 / 文件级 | `quality.extraction_coverage` | 已实现 |
| **利用率** utilization | 这一份合同里的信息，提出来了多少？ | 纵向 / 文件内 | `utilization`（本规范） | 未实现 |

两者不能互相替代。一份 200 页扫描合同，文字层提取成功 → 完整性记 100%，
但公章位置、手写签名、骑缝章、附件表格全未提取 → 利用率可能只有 40%。
**只报完整性会让"没读到"伪装成"读完了"**，这与项目「不假装成功」的核心承诺冲突。

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

## 三、信号清单：利用率的分母

**分母必须是探测到的事实，不能是估计或经验值**——这是本规范的底线，与
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
   不得为了求分母去启动重型 worker。
2. **三态**：每个信号取 `present` / `absent` / `unknown`。探测器不确定时必须记 `unknown`，
   **不得默认 absent**——否则分母被人为压小，利用率虚高。
3. **落盘**：探测结果写入 manifest item 的 `signals` 字段，与 `detected_type` 同级，供路由与报告共用。
4. **分母 = 调度过滤器**：同一份探测结果既是利用率分母，也是增益轨的触发条件——
   探测到 0 张图片就不必启动 OCR。这是成本控制的主要来源（见第六节）。

### 3.3 度量

```
item.utilization = 已成功提取的信号数 / 探测为 present 的信号数
```

- `unknown` 信号既不进分子也不进分母，单独计数并在报告中列出（"3 项信号无法探测"）。
- 包级利用率按 item 加权，权重与 kind 相关（合同/证据类 > 说明文档），权重表随实现确定。
- 利用率**独立于八维质量**，不作为第九维：八维答"提得好不好"，利用率答"提了多少"，正交。

## 四、增益轨

`gateway.routing.ROUTE_RULES` 的动作从三种扩展为四种：
`parse`（主轨内容解析）/ `expand`（容器安全展开）/ **`enrich`（增益轨）** / `unresolved`。

规则：

1. **触发条件 = 信号 present 且主轨未覆盖该信号**。主轨已提取的信号不重复跑。
2. **粒度尽可能细**：页级 / 区域级优先于文件级。例：只对"含 image XObject 且文字层未覆盖"的页做 OCR，
   而不是整份 200 页 PDF 重跑。
3. **产物是独立 rendition，不合并**。多个 rendition 各自保留身份与 lineage，
   检索层做 union（`adg search --all-renditions`）。
   **理由**：合并会破坏 `rendition_id` 的内容哈希语义与 lineage 的可追溯性，
   这是项目最值钱的两个不变量（见 [ADR 0001](../adr/0001-contract-authority-and-write-ownership.md) 第 6 条）。
4. **增益失败不影响主轨结论**：主轨已成功的 item 不因增益轨失败而降级，但缺口必须记入利用率。

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

**预算耗尽必须显性化**：未执行的增益任务记入利用率缺口并出现在报告中，
不得静默跳过——静默跳过就是假装读完了。

### 6.2 三档 profile

`adg parse --profile fast|balanced|quality`（默认 `balanced`）：

| 档 | 行为 | 成本 | 适用 |
|----|------|------|------|
| **快速** fast | 只跑主轨；**仍执行信号探测并报告缺口**，但不实际增益 | ≈ 当前 | 先摸清一包材料的规模与缺口 |
| **均衡** balanced | 主轨 + 探测门控的增益轨；页级/区域级；仅低置信度处升级 | 中 | 默认 |
| **质量** quality | 全部可用轨 + 交叉校验 + 分歧检测 | 高 | 重点材料、开庭前核对 |

两条不变量：

1. **快速档也必须报告利用率缺口**。"快速"的含义是"我知道还有什么没提取，但你让我先跑快的"，
   **不是**"我假装提完了"。这是本规范与「不假装成功」承诺的接口。
2. **档位可按路径/kind 覆盖**：`adg parse --profile fast --focus 证据三/ --focus-profile quality`
   —— 重点材料吃质量档，其余吃快速档。这是最省钱的实际用法。

## 七、验证（实现时补入验证矩阵）

| 判据 | 方法 |
|------|------|
| 分母是事实 | 构造含 N 张图 / M 处批注的 fixture，探测结果必须与手工清点一致 |
| 探测不确定不虚高 | 构造探测器读不了的样本，信号须记 `unknown` 且不进分母 |
| 增益轨不覆盖主轨 | 主轨成功后增益轨产物必须是新 rendition，主轨 rendition_id 不变 |
| 探测门控真的省 | 无图 PDF 必须零次 OCR 调用 |
| 快速档诚实 | `--profile fast` 下利用率缺口仍完整出现在报告中 |
| 预算耗尽显性 | 人为设小预算，未执行项必须出现在缺口清单而非消失 |
| 分歧不自动裁决 | 构造两轨读数冲突样本，产物须同时保留两个读数并标记需人工核对 |
