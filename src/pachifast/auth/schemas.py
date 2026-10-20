import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints
from pydantic.functional_validators import AfterValidator, BeforeValidator

from pachifast.database import AliasedBaseModel

UsernameStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=150), AfterValidator(str.lower)
]
# La lunghezza minima e' imposta da `validate_password_policy` (setting `password_min_length`).
PasswordStr = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


def _empty_str_to_none(v: object) -> object:
    return None if isinstance(v, str) and not v.strip() else v


NullableEmailStr = Annotated[EmailStr | None, BeforeValidator(_empty_str_to_none)]


class RefreshToken(BaseModel):
    refresh_token: str


class RefreshBody(BaseModel):
    """Body of ``/refresh-token`` and ``/logout``: the token is optional because it may come from the cookie."""

    refresh_token: str | None = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None


class TokenData(BaseModel):
    user_id: uuid.UUID | None = None
    username: str | None = None


class UserBase(AliasedBaseModel):
    username: UsernameStr
    email: NullableEmailStr = None
    first_name: str | None = Field(default=None, max_length=150)
    last_name: str | None = Field(default=None, max_length=150)
    is_active: bool | None = True
    is_staff: bool | None = False
    is_superuser: bool | None = False


class UserCreate(UserBase):
    password: PasswordStr


class UserUpdate(AliasedBaseModel):
    email: NullableEmailStr = None
    first_name: str | None = Field(default=None, max_length=150)
    last_name: str | None = Field(default=None, max_length=150)
    password: PasswordStr | None = None
    is_active: bool | None = None
    is_staff: bool | None = None
    is_superuser: bool | None = None


class PasswordChange(AliasedBaseModel):
    """Body of ``POST /auth/user/password``."""

    current_password: PasswordStr
    new_password: PasswordStr


class ProfileUpdate(AliasedBaseModel):
    """Body of ``PATCH /auth/user``: what a user may change about themselves (never a privilege or the username).

    Changing the email needs ``currentPassword``: the email receives the one-time codes of the email MFA method, so
    a stolen access token must not be able to move it.
    """

    email: NullableEmailStr = None
    first_name: str | None = Field(default=None, max_length=150)
    last_name: str | None = Field(default=None, max_length=150)
    current_password: PasswordStr | None = None
    #: the fields of the project's profile (``register_profile``); a ``422`` if none is registered
    profile: dict[str, Any] | None = None


class UserRead(UserBase):
    id: uuid.UUID
    email_verified_at: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    permissions: list[str] = []
    #: the profile of the project (``register_profile``); ``null`` when none is registered
    profile: dict[str, Any] | None = None


class PermissionRead(AliasedBaseModel):
    id: int
    codename: str
    description: str


class PermissionWrite(AliasedBaseModel):
    codename: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=500)


class GroupRead(AliasedBaseModel):
    id: int
    name: str
    description: str
    permissions: list[PermissionRead] = []


class GroupWrite(AliasedBaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=500)
    permission_ids: list[int] = []


class UserGroupsWrite(AliasedBaseModel):
    group_ids: list[int]


class UserPermissionsWrite(AliasedBaseModel):
    permission_ids: list[int]


class MfaRequiredResponse(BaseModel):
    mfa_required: Literal[True] = True
    mfa_token: str


class UserSettings(AliasedBaseModel):
    user_id: uuid.UUID
    theme: str | None = "light"
    notifications_enabled: bool | None = True


class SettingBody(AliasedBaseModel):
    """Body of ``PUT /user/settings/{category}/{key}``: ``{"value": ...}``; ``null`` is a value (back to unset)."""

    value: str | int | float | bool | None
