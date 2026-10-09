"""Small JSON-Schema helpers: validation, strict-mode conversion, and example synthesis.

Only the subset Lark's own schemas use is supported: object/array/string/number/
integer/boolean/null types (including type lists), enum, properties, required, items.
"""

from __future__ import annotations

import copy
from typing import Any

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _types(schema: dict) -> list[str]:
    t = schema.get("type")
    if t is None:
        return []
    return t if isinstance(t, list) else [t]


def _is_type(value: Any, name: str) -> bool:
    if name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, _PY_TYPES.get(name, (object,)))


def validate(data: Any, schema: dict | None, path: str = "$") -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    if not schema:
        return []
    errors: list[str] = []
    types = _types(schema)
    if types and not any(_is_type(data, t) for t in types):
        return [f"{path}: expected {'/'.join(types)}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} not in {schema['enum']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: missing required '{key}'")
        for key, sub in (schema.get("properties") or {}).items():
            if key in data:
                errors.extend(validate(data[key], sub, f"{path}.{key}"))
    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(data):
            errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errors


def strictify(schema: dict) -> dict:
    """Convert to OpenAI strict structured-output form: every object closes its
    properties and requires all of them; originally-optional fields become nullable."""
    s = copy.deepcopy(schema)

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if "object" in _types(node) or "properties" in node:
            props = node.get("properties") or {}
            required = set(node.get("required", []))
            for key, sub in props.items():
                walk(sub)
                if key not in required:
                    t = _types(sub)
                    if t and "null" not in t:
                        sub["type"] = [*t, "null"]
                    if "enum" in sub and None not in sub["enum"]:
                        sub["enum"] = [*sub["enum"], None]
            node["required"] = list(props.keys())
            node["additionalProperties"] = False
        if isinstance(node.get("items"), dict):
            walk(node["items"])

    walk(s)
    return s


def synthesize(schema: dict | None, *, key: str = "") -> Any:
    """A minimal deterministic value satisfying `schema`."""
    if not schema:
        return {}
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    types = [t for t in _types(schema) if t != "null"] or _types(schema) or ["object"]
    t = types[0]
    if t == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        return {k: synthesize(v, key=k) for k, v in props.items()}
    if t == "array":
        items = schema.get("items")
        min_items = schema.get("minItems", 0)
        return [synthesize(items, key=key) for _ in range(min_items)] if isinstance(items, dict) else []
    if t == "string":
        return f"fake {key}".strip() if key else "fake"
    if t == "integer":
        return int(schema.get("minimum", 0))
    if t == "number":
        return float(schema.get("minimum", 0.0))
    if t == "boolean":
        return False
    return None
