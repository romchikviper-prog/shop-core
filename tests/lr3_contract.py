"""Мінімальний перевіряльник JSON Schema для тесту контракту.

Підтримує саме ті ключові слова, які використані в openapi.yaml:
`$ref`, `type`, `required`, `properties`, `additionalProperties`,
`enum`, `pattern`, `minLength`, `maxLength`, `format`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any]) -> None:
    """Кидає AssertionError, якщо instance не відповідає schema."""
    errors: list[str] = []
    _validate(instance, schema, root, "$", errors)
    assert not errors, "; ".join(errors)


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    assert isinstance(reference, str) and reference.startswith("#/")
    node: Any = root
    for part in reference[2:].split("/"):
        node = node[part]
    assert isinstance(node, dict)
    return node


def _validate(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    schema = _resolve(schema, root)
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            errors.append(f"{path}: очікується object")
            return
        _validate_object(instance, schema, root, path, errors)
        return
    if expected_type == "array":
        if not isinstance(instance, list):
            errors.append(f"{path}: очікується array")
            return
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                _validate(item, item_schema, root, f"{path}[{index}]", errors)
        return
    if expected_type == "string":
        if not isinstance(instance, str):
            errors.append(f"{path}: очікується string")
            return
        _validate_string(instance, schema, path, errors)
        return
    if expected_type == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{path}: очікується boolean")
        return
    if expected_type == "integer":
        if isinstance(instance, bool) or not isinstance(instance, int):
            errors.append(f"{path}: очікується integer")
        return
    if expected_type == "number":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            errors.append(f"{path}: очікується number")
        return

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} не входить до enum")


def _validate_object(
    instance: dict[str, Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if key not in instance:
                errors.append(f"{path}: відсутнє поле {key!r}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return
    for key, value in instance.items():
        if key in properties and isinstance(properties[key], dict):
            _validate(value, properties[key], root, f"{path}.{key}", errors)
        elif schema.get("additionalProperties") is False:
            errors.append(f"{path}: зайве поле {key!r}")


def _validate_string(
    instance: str,
    schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if "minLength" in schema and len(instance) < int(schema["minLength"]):
        errors.append(f"{path}: коротший за minLength")
    if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
        errors.append(f"{path}: довший за maxLength")
    if "pattern" in schema and re.fullmatch(str(schema["pattern"]), instance) is None:
        errors.append(f"{path}: не відповідає pattern {schema['pattern']!r}")
    if schema.get("format") == "uuid":
        try:
            UUID(instance)
        except ValueError:
            errors.append(f"{path}: некоректний uuid")
    if schema.get("format") == "date-time":
        try:
            parsed = datetime.fromisoformat(instance)
            if parsed.tzinfo is None:
                errors.append(f"{path}: date-time без часової зони")
        except ValueError:
            errors.append(f"{path}: некоректний date-time")
