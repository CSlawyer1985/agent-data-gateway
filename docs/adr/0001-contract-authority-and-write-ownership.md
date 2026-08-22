# ADR 0001：契约权威与写入所有权

- 状态：Accepted
- 日期：2026-08-22

## 背景

ADG 同时维护 Worker Protocol、PackageManifest、EvidenceDocument 和人类可读说明。若这些表面缺少明确优先级、变更权限和写入者规则，运行时会出现“文档严格、实现宽松”或多个进程互相覆盖事实的情况。

## 决策

1. `models/*.json` 是结构契约的规范来源；`docs/spec/` 定义语义和不变量；运行时代码必须执行两者，README 只是投影视图。三者冲突时不得宽松猜测，必须修正冲突并用测试固定。
2. Worker 只拥有解析/转换建议值；Gateway 拥有协议录取、身份、锚点、最终质量、谱系和 Manifest 状态的定稿权。
3. 原始字节拥有 `source_id` 的事实权；验证后的 EvidenceDocument 是 rendition 事实；PackageManifest 是包级已接受状态和引用索引。
4. 同一输出目录只有一个写入者。原子文件替换解决单文件提交，输出目录锁解决读-改-写所有权，加载时 payload hash 解决陈旧写入检测。
5. 当前仓库维护者是契约变更的决策角色。破坏性 Schema/Protocol 变更必须使用新版本、迁移/兼容策略、ADR 和负向测试；不能原地扩大旧版本含义。对现有实现已经产生、但旧 Schema 遗漏的字段，可作为一致性修正补齐并记录。
6. `rendition_id` 只拥有内容、annotations 与坐标系身份；质量、parser 记录、run_id 和 lineage 由 Gateway 定稿。普通重跑复用同一 rendition，显式 `--force` 是刷新其非身份字段的唯一入口，并必须保留旧 lineage、重算 payload hash、经输出锁原子替换。
7. 格式识别事实由 `gateway.magic` 拥有，操作路由事实由 `gateway.routing.ROUTE_RULES` 拥有，worker 清单只拥有能力与运行要求。Discoverer、Dispatcher、`adg routes` 和测试必须读取同一份路由规则；README/spec 不另维护可执行链表。

## 后果

- 未知版本、损坏哈希、错误 job ID 和越界 Worker 产物会显式失败，不再静默降级为可信事实。
- 写入任务需要处理 `ConcurrentWriteError`，而不是假设最后写入者获胜。
- 新 Worker 和新持久化字段必须先通过统一录取/Schema 测试，不能直接由消费者自行解释。
- 新格式需要同时说明识别方式、路由条件、处理动作与理由；“能识别”“已配置处理链”“本机可用”分别查询，不再由一张手工 README 表混合表达。
