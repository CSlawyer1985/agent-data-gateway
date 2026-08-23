# EvidenceDocument 数据模型（spec 02）

> 统一结构化文档对象。机器可读 Schema：`models/evidence_document.v0.1.json`（normative，冲突时以 Schema 为准）。Markdown/纯文本只是导出视图之一。

## 字段总览

| 字段 | 含义 |
|------|------|
| `schema_version` | 固定 `"0.1"` |
| `source_id` / `rendition_id` / `run_id` | 三身份，见 spec 01 |
| `source` | 包内相对路径（隐私安全）、类型检测、字节数、mtime、容器来源 origin / 转换来源 derived_from |
| `parser` | name/version/worker_id/fell_back_from（降级链记录） |
| `provenance.coordinate_system` | 坐标系明示：name/units/origin |
| `content.blocks[]` | 结构化内容块（page/section/sheet/message_group/segment/stream），每块含 elements[] |
| `annotations` | 多轨：comments / revisions / ocr_boxes / seals / embedded_objects / alternative_renditions |
| `quality` | 八维质量 + fitness 适用性，见 spec 05 |
| `lineage[]` | 处理谱系，每次转换一条 |
| `integrity` | payload_sha256 自校验 + source_sha256 冗余校验 |

## content.blocks 与 elements

块是逻辑容器，按格式选择：

| kind | block_type | 元素定位器 |
|------|-----------|-----------|
| pdf / image | `page` | page_bbox（page + bbox，0-1000 归一化，左上原点） |
| docx / text / html | `section` | paragraph（index） |
| pptx | `page` | paragraph（全局 index；`attrs.slide` 保存幻灯片页码） |
| xlsx / csv | `sheet` | cell（sheet + A1）/ row（index） |
| chat | `message_group` | message（id / timestamp） |
| audio / video | `segment` | timecode（start/end 秒） |
| dxf | `section` | entity（handle / layer） |
| email | `section` + `attachment_list` | paragraph + 附件条目 |

元素 `id` 为确定性派生（spec 07）。元素 `attrs` 承载类型相关属性（style、cells、sender、duration 等）。

## 不变量

1. source_id 身份稳定：任何 parser 产出的 rendition 的 source_id 必须与原件字节哈希一致。
2. 元素确定性 ID：同 parser/版本/配置可重复。
3. 坐标系明示：禁止无坐标系声明的 bbox。
4. payload_sha256 自校验：写入前计算，读取时可验证。
5. 质量多维 + fitness：不允许只有单一分数。
6. lineage 记录每次转换：谁提取的、哪个版本、实际顶层依赖版本、是否经 OCR、输入输出哈希。
7. `authenticity_assessment` 恒为 false——摄取 ≠ 取证。

## 读取与兼容性

- 每次读取必须按对应版本的机器 Schema 校验，并重算 `payload_sha256`、`rendition_id` 与 `source_sha256`；任何一项不一致都作为持久化事实损坏显式失败，不得静默跳过。
- 当前运行时只接受 `schema_version="0.1"`。未来破坏性变更必须发布新版本 Schema、迁移说明和 ADR；不得把未知版本按当前模型宽松加载。

## 隐私

`source.package_relative_path` 是唯一允许出现的路径形态。任何绝对路径、用户名、磁盘位置不得进入本文档（spec 09）。
