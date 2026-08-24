# ADR 0005：工作流由 profile 选择，而非由信号缺口驱动

- 状态：Accepted（已实现：`routing.resolve_plan` 出计划、dispatcher 多链执行、`adg parse --profile`）
- 日期：2026-08-23
- 修订：[ADR 0002](0002-utilization-as-first-class-goal.md) 决策 2 的触发机制；决策 4、5 不变
- 相关：[ADR 0004](0004-utilization-is-not-a-ratio.md)（撤销比值），[spec 08](../spec/08-routing.md)（路由权威）

## 背景

[ADR 0002](0002-utilization-as-first-class-goal.md) 决策 2 设想的增益轨是**缺口驱动**的：
主轨成功后，看还缺哪些信号，触发对应的增益。这要求一张「信号 ↔ worker 能力」的映射，
而 [ADR 0004](0004-utilization-is-not-a-ratio.md) 决策 5 已经否决了它——把 `handles`
细化到信号级只能换来「工具自称能处理该信号」，是用一个可疑的量替换一个不可知的量。

于是 ADR 0002 决策 2 悬空：机制的触发条件没有了。

同时，[spec 08](../spec/08-routing.md) 的既有结构已经比想象中完整。两级路由都在：
`gateway.magic` 定 kind，`preflight` 特征（`pdf_class` 等）在 kind 内选链。而每条链
**内部是同类工具**：

```
pdf-digital-simple   docling → pdfplumber → pypdf → pdf_lite    全在提文字层
image-default        paddleocr → rapidocr → image_lite          全是 OCR
pdf-scanned          mineru → pdf_render                        全在处理扫描版
```

缺的从来不是「知道缺什么」，而是**能不能一次跑多条链**，以及**跑几条由谁决定**。

## 决策

1. **不引入「工作流」这个新名词。** 一条 `RouteRule` 的工具链就是工作流；它现在叫「降级链」，
   只是因为迄今只有 `or` 语义。为「可并跑」再造一个顶层概念会与 `ROUTE_RULES` 争夺同一份
   事实的所有权，而后者已由 [ADR 0001](0001-contract-authority-and-write-ownership.md) 第 7 条
   确立为处理路由的唯一权威。扩展语义，不新增名词。

2. **`parse` 动作从「跑一条链到成功」扩展为「按 profile 跑 N 条链」。** 链内仍是 `or`
   （降级纪律见 spec 08，不变）；链**之间**变成可选的 `and`。多条链各自产出独立 rendition，
   不合并——[ADR 0002](0002-utilization-as-first-class-goal.md) 决策 4 原样适用。

3. **`--profile fast | balanced | quality` 是用户表达策略的旋钮，不是 ADG 的判断。**
   这是 Linux「**机制与策略分离**」(mechanism, not policy) 的直接应用：内核提供机制，
   策略留给用户空间。对应到本项目——
   - **机制（ADG 提供）**：格式识别、特征路由、链的执行、产物定稿、事实记录。
   - **策略（调用方决定）**：用哪个工作流、跑几条、哪个结果可用、够不够、要不要再跑一次。

   三档选的是**工作流**，不只是数量：

   | 档 | 主轨用什么 | 并跑 |
   |----|-----------|------|
   | `fast` | 跳过重工具（`weight=heavy` 或 docker 后端），用链上轻的那些 | 否 |
   | `balanced` | 整条链、先成功先得，拿到效果最好的可用工具 | 否 |
   | `quality` | 整条链 | **是**——多个工作流各自出结果 |

   `fast` 与 `balanced` 的区别在**主轨用什么工具**而不在跑几条：若 `fast` 也走整条链，
   它照样先试最重的工具，就一点都不快。轻重是 worker 的能力事实（`worker.json` 的
   `weight` / 后端），归 worker 清单，不归路由表——路由表只排优先级（spec 08）。
   全链都重时不清空主轨：宁可慢，也不能什么都不做。

4. **ADG 不拥有「哪个已录取结果更好」这个事实。** 多条链产出多个 rendition，ADG 记录每个的来源、
   parser、质量维度与锚点，但不排名、不评分、不选出赢家。判断权属于调用方——它掌握
   ADG 没有的领域知识（这份合同该有哪些要件）。这是 ADR 0002 决策 5「分歧只记录不裁决」
   在全部输出上的推广，也是决策 3 中「策略」一侧的必然结论。录取边界不属于排名：完整 Schema、
   非空事实提取、未截断、无未转写正文图片占位符、无生成尾部重复循环、确定性 OCR 不以多数低置信文本框冒充可靠事实等
   下限不满足时，编排器以 `low_quality` 继续链内降级；若整条链都
   未过下限，最后一个低质量结果仍可作为显式降级证据保留，但不会被 `reliably_read` 计为已读到。

   **链的优先级顺序仍归 ROUTE_RULES**，它来自开发期用已知答案的测试材料做的离线评估。
   离线评估与运行时评判是两件事：前者的 ground truth 在测试集里，可控；后者的 ground truth
   在用户材料里，不可知（ADR 0004）。ADG 固化前者，拒绝后者。

5. **少跑必须显性。** 当 profile 要求的链因 worker 不可用、超时或预算耗尽而未执行，
   manifest 必须记录「该链存在但未执行」及原因，不得静默省略——静默省略等同于假装跑过了。
   这与 spec 08 降级纪律第 3 条「不假装成功」同源，但语义不同：**并跑未执行不是降级**，
   不写入 `parser.fell_back_from`（那是链内回退的记录），需要独立的执行记录字段。
   落地为 `Item.chain_executions`，其中 `scope` 区分两种少跑：整条链没跑（这个角度没看过）
   与链内某个重工具没用上（看了，但没用最好的工具）。二者语义不同，不可混为一谈。

## 后果

- ADR 0002 决策 2 的**触发机制**被本 ADR 取代：不再需要「信号 ↔ 能力」映射，
  也不再需要 `enrich` 这个第四种 action。多视角提取的方向不变，换了触发方式。
  净变化是**减少**：去掉一张映射表和一个 action 取值，换来一个用户可见的 flag。
- 信号探测（spec 11 §3）保留且角色回归本职：它是**路由的输入**（`pdf_class` 本就由
  `preflight` 探测得出），以及探测门控的依据（探测到没有图就不必启动 OCR）。
  它不再承担任何度量职责。
- **契约面**：`--profile` 是新的公开 CLI 表面；`RouteRule` 需要表达「本规则的链可与哪些
  规则并跑」；manifest 需要一个执行记录字段承载决策 5。`renditions` 是数组，多产物不需要
  Schema 变更。`action` 的取值集合不变（不加 `enrich`）。
- **负向路径**：并跑 N 条，部分成功部分失败时，item 状态由成功的那些定稿（现有纪律不变）；
  失败的链进执行记录而非 `fell_back_from`。全部失败仍是 `unresolved`。
- **守卫**：需要三条可证伪的检查——(a) 输出中不出现任何跨 rendition 的排名或评分；
  (b) profile 要求但未执行的链必须出现在执行记录中，人为让某 worker 不可用即可植入违例；
  (c) 并跑产物各自保留独立 `rendition_id`，主轨的 id 不因增加并跑链而改变。
  (a) 已有前置守卫（`signals` 模块不暴露比值 API，ADR 0004）。
- 本 ADR 只确立机制与权属，不锁定三档各跑几条链、也不锁定并跑的并发与预算策略——
  那些随实现调整，以 spec 08 与 spec 11 §6 为准。当前“并跑”指独立 rendition 的 `and` 语义，
  调度器按计划顺序执行，不承诺墙钟时间并发。
