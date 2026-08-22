# 路由策略（spec 08）

> 路由依据是**文档特征和任务目标**，不只是“最强 parser 是否能运行”。每种格式簇配置“特征路由 + 降级链”。

## 权威来源与查询入口

- `gateway.magic` 是格式识别事实的权威：先看魔数/容器/内容特征，扩展名仅作低置信提示与二级消歧。
- `gateway.routing.ROUTE_RULES` 是处理路由的唯一权威：规则顺序就是优先级，Discoverer、Dispatcher 和查询命令读取同一份数据。
- `workers/*/worker.json` 与 lite worker 注册表声明 worker 能力和运行要求，但不自行决定路由优先级。
- 本文与 README 是语义说明，不另维护一份可执行链表。

查看当前项目配置的完整总表：

```bash
adg routes                         # 人类可读：格式 → 条件 → 动作 → 工具 → 原因
adg routes --kind pdf              # 只看某个类型
adg routes --format .msg --json    # Agent/程序可消费的过滤结果
adg check                          # 另查这些 worker 在当前机器是否可用
```

“支持”分为三层，不能混为一谈：

1. `gateway.magic` 能识别该格式；
2. 总路由表为它配置了 `parse` 或 `expand` 动作；
3. 对应 worker 在当前机器可用。

`adg routes` 回答前两层，`adg check` 回答第三层。能识别但没有安全处理路径的格式，规则必须标为 `unresolved`，不能假装支持。

## PDF 特征路由（范例）

```text
PDF 预检（加密检测、文字层采样、版面复杂度、扫描比例）
├── 加密                    → [pypdf, pdfplumber] 先试空用户口令/给定口令 → qpdf（需密码）→ 否则 unresolved(encrypted)
├── 结构不完整              → [pypdf, pdf_lite]
├── 扫描/无文字层           → [mineru, pdf_render]（无容器/无 GPU 时渲染页位图，回灌 image 链 OCR）
├── 混合或复杂版面          → [mineru, docling, pdfplumber, pypdf, pdf_lite]
└── 数字简单                → [docling, pdfplumber, pypdf, pdf_lite]
```

预检启发式（pdflite）：采样内容流解压，统计每页文字字节与图像 XObject 比例。启发式结论写 `low_confidence` 提示，路由决定记录进 lineage 与 quality.notes。

## 总表字段与动作

每条规则至少包含稳定 `rule_id`、`kind`、格式/特征条件、动作、工具链和选择原因。规则按声明顺序首条命中，因此更具体的格式/特征规则必须排在默认规则之前。

- `parse`：把 `workers` 作为实际 Dispatcher 降级链。
- `expand`：容器在发现阶段用 `tools` 安全展开，成员重新走格式识别和总路由。
- `unresolved`：已识别但没有获准的安全处理路径，显式记录能力缺口。

扩展名列表由 `gateway.magic.extension_catalog()` 投影到总表，不在路由模块重复维护。内容魔数优先于扩展名，所以改名文件仍按真实类型路由。

## 降级纪律

1. 先按文档特征选最优链；链内 parser 硬件/可用性不满足时才沿链回退——硬件降级是二级机制。
2. 每次回退记录：`parser.fell_back_from[] = {name, version, reason}`，reason ∈ unavailable / hardware_unmet / timeout / error / rejected_by_preflight / low_quality。
3. 全部失败 → item unresolved，原因 + 建议动作写入 manifest，**不假装成功**。
4. lite parser 产物必须带降级质量标注（如 pdflite：layout_preservation=fail、quotation=false）。

## transform 链

transform worker 的产物（dxf、eml、wav）回灌为新 item（origin.container_kind=transform），继续走目标格式链。来源记录 `source.derived_from`。
