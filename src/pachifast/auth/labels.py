"""Translatable names of permissions and groups.

Permissions and groups are rows created at run time, so their texts cannot be written in the code. What is stable is
the identifier: the ``codename`` of a permission and the ``name`` of a group. When it is a valid key (letters, digits
and ``_``; a permission codename may also have dots), the catalogs can translate it with these keys:

- ``permissions.<codename>.label`` / ``permissions.<codename>.description``
- ``groups.<name>.label`` / ``groups.<name>.description``

The responses keep the stored fields untouched (an edit form sends them back) and add ``label`` and
``localizedDescription`` in the language of the request, with their keys ``labelKey`` and ``descriptionKey`` for
clients that translate by themselves. With no catalog text the label falls back to the identifier and the localized
description to the stored one. An identifier that is not a valid key (``"Billing admins"``) has no keys.
"""

import re
from collections.abc import Mapping
from typing import Any

from pachifast.i18n import lookup

_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _base_key(root: str, identifier: str, *, dots: bool) -> str | None:
    """``<root>.<identifier>`` when the identifier can be part of a message code, else ``None``."""
    parts = identifier.split(".") if dots else [identifier]
    return f"{root}.{identifier}" if all(_SEGMENT.match(part) for part in parts) else None


def permission_key(codename: str) -> str | None:
    """Base message code of a permission (``permissions.<codename>``), ``None`` if the codename is not a valid key."""
    return _base_key("permissions", codename, dots=True)


def group_key(name: str) -> str | None:
    """Base message code of a group (``groups.<name>``), ``None`` if the name is not a valid key."""
    return _base_key("groups", name, dots=False)


def _texts(base: str | None, identifier: str, description: str) -> dict[str, Any]:
    """``label``/``localizedDescription`` (and their keys) of an entity, from its catalog entries when it has any."""
    if base is None:
        return {"label": identifier, "localizedDescription": description}
    return {
        "label": lookup(f"{base}.label") or identifier,
        "labelKey": f"{base}.label",
        "localizedDescription": lookup(f"{base}.description") or description,
        "descriptionKey": f"{base}.description",
    }


def permission_view(permission: Any) -> dict[str, Any]:
    """A permission (model or mapping with ``id``, ``codename``, ``description``) with its translatable texts."""
    data = permission if isinstance(permission, Mapping) else permission.__dict__
    codename, description = data["codename"], data.get("description", "")
    return {
        "id": data["id"],
        "codename": codename,
        "description": description,
        **_texts(permission_key(codename), codename, description),
    }


def group_view(group: Mapping[str, Any]) -> dict[str, Any]:
    """A group dictionary (``id``, ``name``, ``description``, ``permissions``) with its translatable texts."""
    return {
        "id": group["id"],
        "name": group["name"],
        "description": group["description"],
        **_texts(group_key(group["name"]), group["name"], group["description"]),
        "permissions": [permission_view(p) for p in group.get("permissions", [])],
    }
