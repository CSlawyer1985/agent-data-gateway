# ADG 官网部署

官网：https://adg.chenshi.ai/

备用地址：https://agent-data-gateway.pages.dev/

## 部署配置

| 配置 | 值 |
| --- | --- |
| 托管 | Cloudflare Pages |
| 项目 | `agent-data-gateway` |
| 源码 | `CSlawyer1985/agent-data-gateway` |
| 生产分支 | `main`，推送后自动发布 |
| 构建命令 | 留空（纯静态） |
| 输出目录 | `site` |
| 自定义域名 | `adg.chenshi.ai` |
| DNS | CNAME → `agent-data-gateway.pages.dev`，Cloudflare 代理开启 |

`site/` 是唯一发布单元。仓库根目录、用户材料、工作底稿、本地规则及 `promo/` 视频母版不部署到 CDN。网站是介绍页，材料解析继续在用户本机运行。

## 更新和验收

1. 修改网站或文档，运行项目所需检查，审查完整差异后推送 `main`。
2. 在 Pages 确认生产部署对应最新提交且部署成功。
3. 用 `curl -I https://adg.chenshi.ai/` 检查 HTTPS 与 HTTP 状态；核对页面内容和静态资源，检查桌面、手机、管线切换、对比闸门、命令复制及视频播放。
4. GitHub CI 与 Pages 独立运行，Pages 成功不代表核心测试已通过。

首次发布（2026-10-17）的验证结果：本机 `python3 -m unittest discover -s tests` 为 246 项通过；`python3 -m gateway.cli verify` 为 27/28 通过、0 失败、1 跳过；现有 setuptools 后端成功构建 wheel/sdist，MIT 许可元数据及 worker 打包清单正确。

历史 CI 基线问题：Linux 环境的 `test_profile_execution.StatusTellsTheTruthTest` 中 `test_manifest_and_report_show_executed_and_skipped_chains`、`test_quality_may_claim_it_when_nothing_was_skipped` 失败。发布前的 [基线运行](https://github.com/CSlawyer1985/agent-data-gateway/actions/runs/32714865234) 与 [首次官网发布运行](https://github.com/CSlawyer1985/agent-data-gateway/actions/runs/39034933406) 在相同两项断言失败。首次官网发布没有修改核心代码、测试、CI 或依赖；当时远端后续 build/twine 步骤未执行。

后续已定位并修复测试的环境依赖：该测试验证链执行契约，却隐式依赖开发机安装的 `pdfplumber` 等可选工具；干净 CI 的 `pdf_lite` 质量失败属于正确行为。现改用明确的可靠测试 worker，保留原 12 项断言；新增两项无可靠 worker 时不假成功的回归测试。生产代码、质量标准、CI 配置与依赖不变。当前检查状态请查看 [最新 main CI](https://github.com/CSlawyer1985/agent-data-gateway/actions?query=branch%3Amain)。

## 回退

后续网站发布有问题时，在 Cloudflare Pages 的生产部署历史中选择上一个成功版本执行 Rollback。源码修正后再正常提交并推送；不强推或改写 Git 历史。

紧急手动发布（使用已有 Cloudflare 登录态）：

```bash
wrangler pages deploy site --project-name agent-data-gateway --branch main
```

域名变更前保存精确 DNS 记录，只调整 `adg.chenshi.ai`，不要改动其他子域名或 nameserver。
