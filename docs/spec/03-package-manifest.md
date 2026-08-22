# PackageManifest 数据模型（spec 03）

> 包级清单：一个案件材料包对应一份 manifest。机器可读 Schema：`models/package_manifest.v0.1.json`（normative）。

## package_id 计算

```
package_id = sha256( "\n".join( sorted( 所有 item 的 source_id ) ) )
```

包内容身份：增删任何文件（含容器成员）都会改变 package_id。重跑扫描不改变它（内容不变则身份不变）。

## 关键字段

- `stats`：total_items（去重后，含容器成员）、by_kind、total_bytes、dedup_saved。
- `integrity.all_items_accounted`：发现的每个文件都有 item 记录或显式排除记录时为 true。这是“Agent 能否确信没有遗漏”的总开关。
- `integrity.unresolved_items[]`：未能可靠读取的 item + 原因 + 阶段 + 建议动作。**不假装成功**——失败必须显性化。
- `integrity.expanded_archives[]`：scan 阶段登记，含被拒绝成员及原因（安全策略拦截记录）。
- `integrity.expanded_emails[]`：邮件附件在 parse 阶段递归回灌后登记；scan 阶段为 pending。
- `quality`：包级聚合——overall、extraction_coverage（可靠读取占比）、duplicates/empties/encrypted/corrupted/unsupported/low_confidence 清单。
- `items[]`：每个 item 的 source_id、包内相对路径、kind、renditions、status、best_quality。

## item.status 取值

| 值 | 含义 |
|----|------|
| `discovered` | 已发现未解析 |
| `parsed` | 已解析，best rendition 质量 pass |
| `parsed_with_warnings` | 已解析，best rendition 质量 pass_with_warnings |
| `failed` | 解析失败（所有候选 parser 均失败） |
| `unresolved` | 未能可靠读取（加密/损坏/无可用 parser/安全拦截） |
| `unsupported` | 明确不支持的格式（含原因与建议） |

## 生命周期

1. `adg scan`：发现 + 展开压缩包 → 全量 items（status=discovered），写 manifest.json + manifest.md。
2. `adg parse`：逐项解析，回写 renditions/status/best_quality，登记 expanded_emails，更新 unresolved_items，重算 quality 与 package_id（成员增减时）。
3. `adg report/status/list`：只读 manifest 与 documents/，不重算。

## 写入、校验与恢复

- 同一个 `--out` 同时只允许一个 `scan`/`parse` 写入者；第二个写入任务必须显式失败。Manifest 保存还会核对加载时的 payload hash，防止陈旧的读-改-写覆盖新状态。
- JSON 使用同目录唯一临时文件、fsync、原子替换落盘，避免并发写入共享 `.tmp` 文件。
- 读取 Manifest 时必须校验机器 Schema、`schema_version`、`payload_sha256`、`package_id` 与 rendition 引用是否存在。
- rendition 先于 Manifest 落盘。若进程在两者之间中断，下次 `parse` 会校验并收养同一 `source_id` 下的完整孤儿 rendition，再继续调度；损坏的 rendition 不得自动收养。
- 同一 rendition 普通重跑不改写。只有显式 `--force` 可由 Gateway 在保持 rendition_id 内容身份不变的前提下刷新非身份字段，并保留旧 lineage；写入仍走输出锁、Schema/哈希校验和原子替换。
