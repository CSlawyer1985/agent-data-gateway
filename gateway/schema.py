"""项目冻结 JSON Schema 的零依赖运行时校验。

编排器保持纯 stdlib，因此这里只实现本仓库规范模型实际使用的 JSON Schema
Draft 2020-12 子集。遇到未实现的关键字不会擅自放宽；项目新增关键字时应同时
扩展本校验器及其测试。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import ContractViolation

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
_SUPPORTED_KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "definitions",
    "title", "description",
    "type", "const", "enum", "oneOf",
    "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems",
    "pattern", "minimum", "maximum",
}


class SchemaValidationError(ContractViolation):
    """JSON 值不满足项目冻结 Schema。"""


def load_model_schema(filename: str) -> dict[str, Any]:
    path = MODELS_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_model(instance: Any, filename: str) -> None:
    schema = load_model_schema(filename)
    validate_json(instance, schema, root=schema)


def validate_json(instance: Any, schema: dict[str, Any], *, root: dict[str, Any] | None = None, path: str = "$") -> None:
    root = root or schema
    unsupported = set(schema) - _SUPPORTED_KEYWORDS
    if unsupported:
        raise SchemaValidationError(f"Schema 含未实现关键字: {sorted(unsupported)}")
    if "$ref" in schema:
        target = _resolve_ref(root, schema["$ref"])
        validate_json(instance, target, root=root, path=path)
        return

    if "oneOf" in schema:
        matches = 0
        errors: list[str] = []
        for candidate in schema["oneOf"]:
            try:
                validate_json(instance, candidate, root=root, path=path)
                matches += 1
            except SchemaValidationError as e:
                errors.append(str(e))
        if matches != 1:
            detail = errors[0] if errors else "匹配多个候选"
            raise SchemaValidationError(f"{path}: oneOf 应恰好匹配一个分支（实际 {matches}）；{detail}")
        return

    expected = schema.get("type")
    if expected is not None and not _matches_type(instance, expected):
        raise SchemaValidationError(f"{path}: 类型应为 {expected!r}，实际为 {type(instance).__name__}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"{path}: 值应为 {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"{path}: 值 {instance!r} 不在允许集合中")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaValidationError(f"{path}: 缺少必填字段 {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_json(value, properties[key], root=root, path=child_path)
            elif additional is False:
                raise SchemaValidationError(f"{path}: 不允许字段 {key!r}")
            elif isinstance(additional, dict):
                validate_json(value, additional, root=root, path=child_path)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: 数组长度小于 {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: 数组长度大于 {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_json(value, item_schema, root=root, path=f"{path}[{index}]")

    if isinstance(instance, str) and "pattern" in schema:
        if re.search(schema["pattern"], instance) is None:
            raise SchemaValidationError(f"{path}: 字符串不匹配 {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: 数值小于 {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: 数值大于 {schema['maximum']}")


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaValidationError(f"不支持的外部 Schema 引用: {ref}")
    current: Any = root
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise SchemaValidationError(f"Schema 引用不存在: {ref}")
        current = current[token]
    if not isinstance(current, dict):
        raise SchemaValidationError(f"Schema 引用不是对象: {ref}")
    return current


def _matches_type(value: Any, expected: str | list[str]) -> bool:
    choices = [expected] if isinstance(expected, str) else expected
    return any(_matches_single_type(value, choice) for choice in choices)


def _matches_single_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise SchemaValidationError(f"Schema 使用了未实现的 type: {expected!r}")
