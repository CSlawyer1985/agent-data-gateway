# 路由策略（spec 08）

> 路由依据是**文档特征和任务目标**，不只是“最强 parser 是否能运行”。每种格式簇配置“特征路由 + 降级链”。

## PDF 特征路由（范例）

```text
PDF 预检（加密检测、文字层采样、版面复杂度、扫描比例）
├── 加密                    → qpdf worker（需密码）→ 否则 unresolved(encrypted)
├── 有可靠文字层、版面简单  → [docling, pypdf, pdflite]
├── 混合型                  → [mineru, docling, pypdf, pdflite]
└── 扫描或复杂版面          → [mineru] → 无可用 worker 时 unresolved，不假装成功
```

预检启发式（pdflite）：采样内容流解压，统计每页文字字节与图像 XObject 比例。启发式结论写 `low_confidence` 提示，路由决定记录进 lineage 与 quality.notes。

## 各簇默认链

| 簇 | 链（按优先级） |
|----|----------------|
| docx | docx_lite（批注/修订多轨）→ officecli → docling |
| xlsx | xlsx_lite → docling |
| csv / txt / md | csv_lite / text_lite |
| html | trafilatura → html_lite |
| image | paddleocr → image_lite（仅元数据） |
| eml | eml_lite |
| msg | extract_msg |
| pst/ost | libpff（transform→eml 回灌） |
| chat 导出 | chat_lite；截图走 image 簇 |
| 音视频 | whisper（+ffmpeg）→ av_lite（仅元数据，text_extraction=fail） |
| dxf | ezdxf → dxf_lite |
| dwg | libredwg（transform→dxf 回灌） |
| rar/7z | py7zr worker → 否则 unresolved |

## 降级纪律

1. 先按文档特征选最优链；链内 parser 硬件/可用性不满足时才沿链回退——硬件降级是二级机制。
2. 每次回退记录：`parser.fell_back_from[] = {name, version, reason}`，reason ∈ unavailable / hardware_unmet / timeout / error / rejected_by_preflight / low_quality。
3. 全部失败 → item unresolved，原因 + 建议动作写入 manifest，**不假装成功**。
4. lite parser 产物必须带降级质量标注（如 pdflite：layout_preservation=fail、quotation=false）。

## transform 链

transform worker 的产物（dxf、eml、wav）回灌为新 item（origin.container_kind=transform），继续走目标格式链。来源记录 `source.derived_from`。
