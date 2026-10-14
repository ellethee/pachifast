"""Re-export: ``AliasedBaseModel`` lives in ``pachifast.schemas.aliased`` (schemas do not depend on the database)."""

from pachifast.schemas.aliased import AliasedBaseModel

__all__ = ["AliasedBaseModel"]
