import re
from typing import Any


def serialize(obj: Any) -> Any:
    """
    Serializza oggetti Pydantic v2 o liste/dict annidate in modo ricorsivo.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    elif isinstance(obj, list):
        return [serialize(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    else:
        return obj


def to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def camelize(obj):
    if isinstance(obj, dict):
        return {to_camel(k): camelize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [camelize(v) for v in obj]
    else:
        return obj


# 1. Le funzioni di utilità per convertire in snake_case
def to_snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


def snakify(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {to_snake(k): snakify(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [snakify(v) for v in obj]
    else:
        return obj
