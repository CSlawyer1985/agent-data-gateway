# 质量模型（spec 05）

> 规范数据：`models/quality_dimensions.v0.1.json`（gateway/quality.py 直接加载执行）。本文是语义说明。

## 顶层三态

`pass` / `pass_with_warnings` / `fail`。

## 八个子维度

| 维度 | 含义 |
|------|------|
| discovery | 文件是否被完整发现（含容器成员递归） |
| type_detection | 类型识别置信度（魔数与扩展名一致性、检测方法） |
| text_extraction | 文本内容是否被提取 |
| layout_preservation | 版面/结构信息保留（坐标、表格结构、分页、图层） |
| ocr | OCR 是否执行及质量 |
| attachments | 附件/嵌入对象是否完整递归回灌 |
| metadata | 元数据（作者、时间、EXIF、邮件头、哈希）提取程度 |
| source_anchoring | 元素能否定位回原件（页/坐标/段落/单元格/时间码/消息 ID） |

取值：`pass` / `warning` / `fail` / `unknown`。

`unknown` 的双重语义由 expected_for 区分：该 kind 不需要此维度（不压级） vs 需要但未执行（压级）。例如 ocr 对 text 不期望，对 image 期望。

**因此 expected_for 的遗漏是安全缺陷而非小瑕疵**：一个有 parser 的 kind 若不在某内容维度的 expected_for 里，该维度 `fail` 会被聚合规则静默丢弃，overall 照样报 `pass`。验证矩阵以 `tests/test_quality.py` 的守卫固定该不变量：**凡有 parse 链的 kind，text_extraction 与 source_anchoring 必须在其 expected_for 内**。`layout_preservation` 不在守卫内——text/csv/email/chat/audio/video 确实没有版面可保留。

## 聚合规则

1. 任一 expected 维度 = fail → overall = **fail**。
2. 无 fail；任一 expected 维度 = warning 或 unknown → overall = **pass_with_warnings**。
3. 全部 expected 维度 = pass → overall = **pass**。

parser 给出建议值，编排器拥有最终定稿权（worker 输出不可信）。

## fitness（适用性）

| 旗标 | 含义 |
|------|------|
| fulltext_search | 可用于全文检索 |
| fact_extraction | 可用于事实提取 |
| quotation | 可可靠引用（锚点可回溯原件） |
| layout_sensitive_analysis | 可支撑版面敏感分析 |
| authenticity_assessment | 恒为 false——摄取 ≠ 取证，需人工/鉴定复核 |

法律意义：一份文件可能可全文检索、可事实提取，但不能可靠引用、不能用于真伪鉴定。例如 pdflite 兜底提取的 PDF 文本：fulltext_search=true、fact_extraction=true、quotation=false（锚点不可靠）、layout_sensitive_analysis=false。

## reliably_read（adg status 判定规则）

```
存在至少一个 rendition：overall ∈ {pass, pass_with_warnings} 且 fitness.fact_extraction = true
```

不满足的 item 进入“未可靠读取”清单并附原因——这是 Agent 查询“哪些文件还没被可靠读取”的判据。
