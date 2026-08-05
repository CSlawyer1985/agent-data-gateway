# 身份模型（spec 01）

> 三种身份拆开，解决“跨解析稳定”与“parser 升级结果变化”的矛盾。本模型是 P0 冻结的第一优先级。

## 三身份

```jsonc
{
  "source_id":    "sha256:<原始文件字节哈希>",      // 原始证据身份，永久稳定，与 parser 无关
  "rendition_id": "sha256:<解析产物规范化哈希>",    // 某 parser+版本+配置 产生的解析版本
  "run_id":       "uuid:<本次处理任务唯一ID>"        // 一次执行记录
}
```

- `document_id` 直接等于 `source_id`——原件身份，永久稳定。
- 解析结果作为 rendition 管理：同一 source_id 可有多个 rendition（不同 parser、不同版本、不同配置）。
- 由此支持：同一原件多 parser 比较、重新解析、parser 升级、OCR 模型替换、结果差异审计。

## rendition_id 计算

对 `{content, annotations, provenance.coordinate_system}` 的 canonical JSON（键排序、无空白、UTF-8、 ensure_ascii=False）取 sha256。同一 parser/版本/配置对同一原件重跑，应得到相同 rendition_id（L2 可重复性的校验基础）。

## run_id 语义

一次 `adg parse` 调用一个 run_id（`uuid:` 前缀 + UUID4）。写入该次产生的所有 EvidenceDocument 与谱系。重跑产生新 run_id，但不改变 source_id；同配置重跑也不改变 rendition_id。

## 三层稳定性

| 层 | 要求 | 说明 |
|----|------|------|
| L1 原件身份 | `source_id` 100% 一致 | 与 parser 无关，永久稳定 |
| L2 同 parser/版本/配置 | `element_id` 可重复 | 同一 rendition 内确定性派生（spec 07） |
| L3 跨 parser | 不要求相同，建立 alignment | `cross_rendition_alignment` + score（spec 07） |

## 派生身份

- 容器成员（压缩包内文件、邮件附件）：成员字节独立计算 source_id；来源关系记录在 `source.origin`，不参与父容器 source_id。
- 转换产物（dwg→dxf、音视频提取音轨）：产物字节独立计算 source_id；来源关系记录在 `source.derived_from`。
- 去重：字节相同的多个路径共享一个 source_id，manifest 记 duplicates，只保留一个 item。
