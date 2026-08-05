# 安全策略（spec 06）

> 规范数据：`models/security_policy.v0.1.json`（gateway/security.py 直接加载执行）。案件材料本质是不可信输入，安全模型第一版即内置。

## 威胁模型

| 威胁 | 防护 |
|------|------|
| ZIP bomb / 解压炸弹 | 成员逐一压缩比检查（>200 拒绝）+ 累计解压总量上限（50GB）+ 单成员上限（2GB） |
| 路径穿越（../、绝对路径、盘符） | 成员名归一化校验，拒绝并记录，不写盘 |
| 符号链接逃逸 | zip/tar 中 symlink/hardlink 成员拒绝解压 |
| 极端嵌套 | 递归深度上限 8 层 |
| 海量成员 | 成员数上限 100 000 |
| Office 宏 / PDF JavaScript | 检测并标记（quality.notes + metadata），永不执行 |
| HTML 外网请求 | 解析时剥离渲染语境，永不主动抓取外链 |
| 内嵌可执行文件 | 标记并拒绝解析 |
| parser 漏洞 / worker 资源耗尽 | 容器沙箱：只读挂载、network=none、限 CPU/内存/PID/时间、tmpfs work_dir、自动清理 |
| 加密文件 | 标记 encrypted，进入 unresolved，不猜测密码 |
| XML 实体炸弹 | 解压字节上限 + 禁用外部实体（stdlib expat 默认不解析外部实体，叠加放大上限） |

## 压缩包参数（默认值）

```yaml
archive:
  max_depth: 8
  max_members: 100000
  max_total_uncompressed_bytes: 50GB
  max_compression_ratio: 200
  max_single_member_bytes: 2GB
  deny_path_traversal: true
  deny_symlink_escape: true
  deny_absolute_paths: true
  deny_device_files: true
  encrypted_members: mark_and_skip
```

## worker 沙箱（默认值）

- docker：read_only root、原件只读挂载、network=none、memory 8GB、cpus 4、pids 256、tmpfs work_dir、drop ALL caps、no-new-privileges、--rm。
- subprocess（降级）：timeout 1800s、stdout/stderr 上限 64MB、独立 work_dir、退出清理、环境变量白名单。

## 解析器结构上限（默认值）

文本 512MB、HTML 128MB、表格 200 万单元格、CSV 500 万行、邮件附件 500 个/单附件 512MB、聊天 200 万条、图片 2 亿像素、docx 50 万段落、PDF 预检 2 万页。超限策略：截断 + warning + 记录 truncated，不崩溃。

## 安全事件记录

所有拦截（rejected_members、encrypted、宏检测、超限截断）必须写入 manifest（包级）与 EvidenceDocument quality.notes（文件级），不得静默丢弃。
