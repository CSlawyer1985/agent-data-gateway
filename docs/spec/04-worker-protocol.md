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
| docker/podman | 强 | 默认。只读挂载原件、network=none、限 CPU/内存/PID、tmpfs work_dir、drop ALL capabilities |
| subprocess | 弱 | 兼容降级。独立 work_dir、超时、输出上限、环境变量白名单、退出清理。network=true 的 worker 禁止此后端 |
| inproc（lite parser） | 无 | 仅限主仓库内纯 stdlib 代码路径，不加载第三方 native 代码 |

## 纪律

1. worker 不得外联网络（声明 network=true 的 enrich 类除外，且必须说明用途）。
2. worker 不得写 work_dir 之外的位置。
3. worker 输出不可信：编排器必须校验片段结构、重算锚点 ID、定稿质量后才落盘。
4. GPL 组件（extract-msg、LibreDWG 等）必须隔离为独立进程，不得以库形式链入编排器。
