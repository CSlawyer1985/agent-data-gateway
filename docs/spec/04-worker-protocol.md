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

`subprocess.probe_import` 只验证模块可被当前 Python 发现，不执行模块顶层代码。重型 ML 包冷导入可能超过一分钟，
不能把冷启动耗时误判成“依赖未安装”；真实导入、模型初始化和推理由 Worker 执行路径负责，失败时按协议显性降级。

可用性判定覆盖三个维度，缺任何一个都是运行时必然失败，必须报不可用而不是等到跑起来才炸：

| 维度 | 依据 | 判定 |
|------|------|------|
| 单模块可发现 | `subprocess.probe_import` | 找不到 → `unavailable` |
| 全部声明依赖已装 | `python_requires` | 逐包读分发元数据（查不到再做一次模块发现），确定缺失 → `unavailable` 并列出缺哪个 |
| 模型权重已就绪 | `prefetch.verify` | 退出码 `0`=就绪 / `1`=确定缺失 → `model_missing` / 其余=查不出来，不据此判不可用 |

三者都只读元数据或做模块发现，不执行模块顶层代码，因此不违反上面的冷导入纪律。`prefetch.verify` 由 worker 作者
提供（能力事实归清单），必须能离线判定：多数上游在运行时不会补下载权重，缺权重却报“可用”就是假装成功。
判定“查不出来”时保持可用并在 detail 说明——探测脚本自身出错不能反过来诬告用户没装模型。

没有 worker 专属 venv 时依赖探测回退到当前解释器（`adg setup --into-current` 的形态），此时 detail 必须显式说明
依赖来自当前解释器：不相干环境里恰好装了同名包，不构成这个 worker 可用的证据。

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
- 第三方推理库的日志、下载提示和进度必须写 stderr；官方 adapter 在调用重型库期间同时重定向 Python 与原生
  stdout 到 stderr。编排器不容忍脏 stdout，以免日志被误录取成协议消息。
- subprocess 与 Docker 共用同一套真实 deadline 和 stdout+stderr 输出上限；静默 Worker 也必须能在 deadline 到达时被停止。
- `fragment_path`、`output_files[].path`、附件路径必须是 `work_dir` 相对路径。编排器拒绝绝对路径、`..`、不存在的文件以及任一路径组件的符号链接；通过录取后才读取内容。
- Docker 内的绝对产物路径只允许位于 `/adg-work`，进入宿主机前转换为相对路径并再次执行 containment 校验。
