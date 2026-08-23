# Worker 开发指南

每个 worker 一个目录：`workers/<name>/worker.json` + `workers/<name>/adapter.py`。

## worker.json 字段

| 字段 | 含义 |
|------|------|
| `name` / `version` | worker 身份（记入 rendition 与降级链） |
| `handles` | `[{"kind": "pdf", "formats": [...], "role": "parse|transform|enrich"}]` |
| `requirements` | `{"min_ram_mb": n, "gpu": "required|optional|none", "backend": ["docker","subprocess"], "network": false}` |
| `subprocess` | `{"adapter": "adapter.py", "venv": ".venv", "probe_import": "包名", "probe_command": ["bin","--version"]}` |
| `docker` | `{"image": "adg-worker-<name>:x.y"}`（存在则 backend 默认 docker） |
| `python_requires` | `adg setup --worker <name>` 装进 `.venv` 的 pip 依赖 |
| `weight` | `light`（--lite 安装）/ `heavy` |
| `maturity` | `stable` / `implemented` / `protocol-smoke` / `experimental`；真实模型未跑 fixture 时不得写 implemented |
| `license` | GPL 组件必须隔离为独立进程（spec 04 §纪律 4） |

## adapter.py 协议（JSON Lines over stdio）

- 入：`capabilities_request` → 出：`capabilities`
- 入：`parse_request` → 出：`progress`* + 终态 `parse_result`×1
- `parse_result`：`status=ok` 带 `fragment`（小产物内联）或 `fragment_path`（写入 work_dir）；
  `status=failed` 带 `error{kind,message}`；transform 角色用 `output_files` 声明产物。

## fragment 契约（parse 角色）

```jsonc
{
  "parser_name": "...", "parser_version": "...",
  "coordinate_system": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
  "content": {"blocks": [{"block_type": "page", "index": 0,
              "elements": [{"type": "paragraph", "text": "...",
                            "locator": {"system": "page_bbox", "page": 0, "bbox": [l,t,r,b]}}]}]},
  "annotations": {"comments": [], ...},          // 可选，部分键
  "quality_dims": {"text_extraction": "pass", ...},  // 建议值，编排器定稿
  "fitness": {...},                              // 外部 worker 只生效保守位（false 覆盖 true）
  "notes": ["..."], "warnings": ["..."], "truncated": false,
  "attachments": [{"name": "原始名", "path": "work_dir 内相对路径"}],  // 回灌（eml 等）
  "output_files": [{"path": "...", "format": "dxf"}]                   // transform
}
```

**元素不带 id**——锚点 ID 由编排器按 spec 07 确定性派生。`_anchor_locator` 可挂在
comments/revisions 上，编排器解析为 `anchor_element_id`。

## 沙箱纪律

worker 不得外联网络（声明 network=true 除外并须说明用途）；不得写 work_dir 之外的位置；
输出不可信，编排器会校验、重算锚点、定稿质量后才落盘。Docker 后端强制前两条；subprocess
无法形成操作系统安全边界，只允许受信任代码，且编排器拒绝 `network=true + subprocess` 组合。
