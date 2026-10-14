"""Pydantic base class with camelCase aliases for the API schemas."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AliasedBaseModel(BaseModel):
    """Schema base: camelCase on the wire, snake_case in Python; ORM rows can be validated directly."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        from_attributes=True,
    )
