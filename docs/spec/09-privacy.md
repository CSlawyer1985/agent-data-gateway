# 隐私保护（spec 09）

> 目标：防止工具的日志、Agent 对话、导出报告泄露用户名、客户名、目录结构、磁盘位置。

## 规则

1. **默认只输出 package_relative_path**：所有 manifest、EvidenceDocument、报告、stdout 摘要中，文件位置一律用包内相对路径（如 `证据三/合同.pdf`）或容器路径（如 `bundle.zip::内层/a.pdf`、`msg::part2/发票.pdf`）。
2. **绝对路径只在本地私有运行日志**：`<out>/logs/run-<run_id>.log`，记录相对路径 ↔ 绝对路径映射，供本地排障。可用 `--no-abs-log` 关闭。
3. **包根目录名只保留 basename**：manifest.package_root_name 不含父路径。
4. **stdout 不打绝对路径**：CLI 摘要中的输出位置用相对或 `~/` 缩写。
5. **worker 协议内部**：parse_request.source_path 是运行时技术细节（容器内为挂载路径），不进入任何持久化产物。

## 校验

验证矩阵含隐私项：扫描 manifest.json / manifest.md / documents/**.json / report.md，正则检查不得出现包外绝对路径（`/Users/<name>/`、`/home/`、盘符）。测试以合成用户名断言。
