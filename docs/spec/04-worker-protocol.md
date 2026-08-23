# Worker 协议（spec 04）

> 编排器与 parser worker 之间的唯一接口：JSON Lines over stdio。机器可读 Schema：`models/worker_protocol.v0.1.json`。

## 角色与职责

- **编排器**：发现、路由、调度、身份与锚点定稿、质量定稿、谱系、manifest。worker 不决定身份与最终质量。
- **worker**：对单个文件执行解析/转换/增强，产出 EvidenceDocument **片段**（content/annotations/quality 建议值/warnings）。大产物写 work_dir 文件。

## 消息流

```text
编排器 → worker:  {"protocol_version":"0.1","type":"capabilities_request"}
worker → 编排器:  {"type":"capabilities","worker":{...},"handles":[...],"requirements":{...}}

编排器 → worker:  {"type":"parse_request","job_id":"...","source_path":"...",
                   "source_id":"...","kind":"pdf","options":{...},"limits":{...},"work_dir":"..."}
worker → 编排器:  {"type":"progress","job_id":"...","pct":42,"stage":"ocr"}     # 0..n 次
worker → 编排器:  {"type":"parse_result","job_id":"...","status":"ok",
                   "fragment_path":"result.json","warnings":[],"metrics":{...}}  # 恰好 1 次终态
```

终态消息有且仅有一个：`parse_result`（status=ok/failed/unavailable）。worker 进程异常退出等价于 status=failed{error.kind=crash}。

## worker 三类角色

| role | 语义 | 产物 |
|------|------|------|
| `parse` | 解析原件 → EvidenceDocument 片段 | fragment / fragment_path |
| `transform` | 格式转换（dwg→dxf、pst→eml、提取音轨） | output_files[]，产物回灌为新 item |
| `enrich` | 补充信号（EXIF、类型识别增强） | fragment（并入既有 rendition） |

## worker manifest（workers/<name>/worker.json）

声明：name/version/handles/requirements（min_ram_mb、gpu、backend、network）、docker（image）、subprocess（command、venv、probe）、weight（light/heavy）、maturity、license、notes。

编排器据此做：可用性探测（`adg check`）、硬件匹配与降级决策（spec 08）、沙箱参数生成（spec 06）。

## 执行后端

| backend | 隔离 | 说明 |
|---------|------|------|
| docker/podman | 强 | 清单选择容器时：只读挂载原件、network=none、限 CPU/内存/PID、tmpfs work_dir、drop ALL capabilities |
| subprocess | 弱 | 受信任代码兼容形态。独立 work_dir、超时、输出上限、环境变量白名单、退出清理；不能阻止进程读取/写入当前用户可访问的其他路径，也不能执行 `network=none` |
| inproc（lite parser） | 无 | 仅限主仓库内纯 stdlib 代码路径，不加载第三方 native 代码 |

## 纪律

1. worker 不得外联网络（声明 network=true 的 enrich 类除外，且必须说明用途）。
2. worker 不得写 work_dir 之外的位置。
3. worker 输出不可信：编排器必须校验片段结构、重算锚点 ID、定稿质量后才落盘。
4. GPL 组件（extract-msg、LibreDWG 等）必须隔离为独立进程，不得以库形式链入编排器。

前两条是 worker 行为纪律；只有容器后端提供操作系统级强制。编排器拒绝 `network=true + subprocess`
组合，但 `network=false` 的 subprocess 仍只允许仓库内或用户明确信任的代码，不能用于对抗恶意 worker。

## 编排器录取规则

- 请求与每一条 progress/终态消息都按 `models/worker_protocol.v0.1.json` 校验；`job_id` 必须与请求一致，stdout 中的非 JSONL、未知字段或未知消息类型均按 `rejected` 失败。
- subprocess 与 Docker 共用同一套真实 deadline 和 stdout+stderr 输出上限；静默 Worker 也必须能在 deadline 到达时被停止。
- `fragment_path`、`output_files[].path`、附件路径必须是 `work_dir` 相对路径。编排器拒绝绝对路径、`..`、不存在的文件以及任一路径组件的符号链接；通过录取后才读取内容。
- Docker 内的绝对产物路径只允许位于 `/adg-work`，进入宿主机前转换为相对路径并再次执行 containment 校验。
