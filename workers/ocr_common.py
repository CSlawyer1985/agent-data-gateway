"""确定性 OCR Worker 共用的最低质量判定。"""

from __future__ import annotations

LOW_CONFIDENCE_THRESHOLD = 0.8


def confidence_quality(boxes: list[dict]) -> tuple[str, int, bool]:
    """多数文本框低于 0.8 时降为 warning，仍保留全文检索价值。"""
    low_confidence = sum(
        1 for box in boxes if float(box.get("confidence") or 0) < LOW_CONFIDENCE_THRESHOLD
    )
    majority_low = bool(boxes) and low_confidence * 2 > len(boxes)
    quality = "pass" if boxes and not majority_low else "warning"
    return quality, low_confidence, majority_low
