# 进度与下一步

> 架构决策看 [`docs/adr/`](adr/)，规范看 [`docs/spec/`](spec/)。本文只记「做到哪了、接着做什么」，
> 随实现推进，不承担契约职责。

## 当前进度（截至 2026-08-23）

### 分发与运行环境（[ADR 0003](adr/0003-distribution-and-target-runtime.md)）— 已定案

调用者是**开发机上的编码 Agent**（Claude Code / Codex 这类），终端用户（律师）不经中间
产品层，直接使用这些 Agent。分发走 `pip` / `uv tool`；冻结打包已实测否决，不做 `.app`、
图形界面、代码签名，也不做 Agent 自动发现入口（不装 skill / 不写 AGENTS.md / 不做 MCP）。

worker 的 venv 落 `ADG_HOME`（默认 `~/.adg`），不落 `site-packages`——`uv tool` / `pipx`
升级会重建环境。清单旁已有 `.venv` 时沿用，仓库内既有开发环境不受影响。

### 信号探测（[spec 11](spec/11-utilization.md) 一期）— 已实现

`gateway/signals.py` 持有信号清单，探测随 `adg scan` 落 manifest 的 `item.signals`（Schema v0.2）。
三态 `present` / `absent` / `unknown`，拿不准一律 `unknown`。

**目前只有 PDF 有探测器**，其余 kind 的信号一律 `unknown`。推广到 docx / xlsx / image / email
是待办项之一。

### 多链并跑与 profile（[ADR 0005](adr/0005-workflow-selection-by-profile.md)）— 已实现

`adg parse --profile fast | balanced | quality`：

| 档 | 主轨用什么 | 并跑 |
|----|-----------|------|
| fast | 跳过重工具（`weight=heavy` 或 docker 后端） | 否 |
| balanced（默认） | 整条链、先成功先得 | 否 |
| quality | 整条链 | 是 |

执行记录落 `item.chain_executions`，`scope` 区分「整条链没跑」与「链内某个重工具没用上」。
`adg status` 会报未执行的链并据此返回退出码 1。

### 利用率比值 — 已撤销（[ADR 0004](adr/0004-utilization-is-not-a-ratio.md)）

不要再尝试实现 `item.utilization`。分子「已成功提取的信号数」需要 ground truth，
而 ADG 若掌握 ground truth 就不必调用这些工具——**原理上不可知**。
`gateway/signals.py` 有一条测试钉住模块不得暴露比值 API；想加回来得先修订那份 ADR。

## 下一步（按优先级）

1. **`adg report` 与 `manifest.md` 呈现执行记录。**
   记录已落 `chain_executions`，但只有 `adg status` 会读。报告和人类可读清单仍看不到
   「这份材料跑了哪几条链、哪条没跑」。`--json` 那边不用动，字段已在。

2. **把并跑声明推广到其他 kind。**
   目前只有 `pdf-digital-simple` 声明了 `parallel_with`（代表样本，已穿过真实验收）。
   docx / xlsx / image 等待同处理。做之前先想清楚各自的「视觉视角」是什么。

3. **信号探测器推广到 docx / xlsx / email。**
   PDF 的做法见 `packs/legal/pdf_lite.py` 的 `_pdf_signals`：复用 preflight 已有的解析，
   不额外读文件；未命中只有在采样可信时才判 `absent`。

4. **spec 11 §5 分歧记录（四期）。**
   复用 `cross_rendition_alignment`。注意 ADR 0004 决策 4：只记录，不裁决。

## 开发环境

项目本身零硬依赖（纯 stdlib），但测试需要 pytest，且它不在默认依赖里：

```bash
pip install -e ".[test]"
python -m pytest -q
```

`adg verify` 跑完整自检矩阵（比单测慢，约 1-2 分钟）：

```bash
python -m gateway verify --work-dir /tmp/adg-verify
```

## 几个已知的坑

- **`--force` 重跑不清理上次并跑产生的派生 item。** 从 `quality` 降到 `fast` 重跑时，
  上一轮的渲染图仍留在 manifest 里。属既有 transform 行为，验证 profile 差异时
  **必须用独立的 `--out` 目录**，否则会得出错误结论。
- **探测目前跑了两次。** scan 阶段探一次落 `signals`，parse 阶段 `dispatcher` 又
  `preflight` 一次做路由。功能正确，但 spec 11 §3.2 第 4 条要的「一次探测，两用」
  还没兑现。
- **新落盘的字段都要过 `scrub_text`。** worker 异常消息里含绝对路径，
  `chain_executions` 曾因此泄漏过一次，被 `test_no_absolute_path_leak` 抓到（spec 09）。
