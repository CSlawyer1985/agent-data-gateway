# 来源锚点（spec 07）

> 每个元素必须能定位回原件。本规范定义定位器（locator）、元素确定性 ID 派生、跨 rendition 对齐。

## 定位器（locator）按格式

| 格式 | locator.system | 字段 |
|------|---------------|------|
| PDF | `page_bbox` | page（0 起）、bbox [l,t,r,b]，0-1000 归一化，左上原点 |
| 图像 | `page_bbox` | page=0、bbox 归一化坐标 |
| docx / 文本 / HTML | `paragraph` | index（文档顺序段落序号） |
| pptx | `paragraph` | index（全局段落序号），元素 `attrs.slide` 保存幻灯片页码 |
| xlsx | `cell` | sheet、cell（A1 表示） |
| csv | `row` | index（0 起，不含表头） |
| 聊天 | `message` | id（序号或导出自带 ID）、timestamp |
| 邮件 | `paragraph` / 附件条目 | index；附件用 container_path（msg::partN/name） |
| 音视频 | `timecode` | start、end（秒，浮点） |
| dxf | `entity` | handle、layer |
| pdflite 兜底 | `stream` | index（近似定位，quality.source_anchoring 必为 warning） |
| 元数据 | `field` | name |

坐标类定位器必须在 `provenance.coordinate_system` 中明示 name/units/origin。

## 元素确定性 ID（L2 可重复性）

```
element_id = "el_" + sha1( parser_name | parser_version | config_hash | locator_canonical | ordinal )[:12]
```

- `config_hash`：parser 生效配置 canonical JSON 的 sha256（前 16 位）。
- `locator_canonical`：定位器字段按键排序的紧凑 JSON。
- `ordinal`：同一块内同定位器的序号（防重复）。
- 同一 parser/版本/配置重跑同一原件 → 全部 element_id 一致。这是验证矩阵 L2 判据。

## 跨 rendition 对齐（L3）

不同 parser 不可能 element_id 一致。同 source_id 存在 ≥2 个 rendition 时，编排器计算对齐表：

```jsonc
{"source_id": "...", "rendition_a": "...", "rendition_b": "...",
 "pairs": [{"rendition_a_element": "el_xxx", "rendition_b_element": "el_yyy",
            "alignment_score": 0.94}]}
```

算法：同 block_type 下按文本相似度（difflib，阈值 0.6）贪心匹配。对齐表存 `documents/<source_id>/alignment-<a>-<b>.json`。只声明“疑似对应”，不断言语义等价。

## 锚点回溯校验

`anchor.validate(doc)`：抽样/全量检查 locator 字段在文档内容范围内可解析（page 不越界、paragraph index 存在、cell 在 sheet 内、timecode start<end）。失败计入 quality.source_anchoring=warning/fail。
