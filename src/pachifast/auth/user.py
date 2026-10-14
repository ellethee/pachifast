from logging import getLogger
from typing import Annotated, Any

from fastapi import Body, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from pachifast.dependencies import CoreSessionDep
from pachifast.i18n import MessageError, api_error, detail_from_error
from pachifast.routing.router_factory import get_router

from . import cruds
from .cruds import SettingValue
from .dependencies import CurrentUserDep
from .permission_provider import resolve_user_permissions
from .profile import get_profile_config, load_profile, save_profile
from .schemas import PasswordChange, ProfileUpdate, SettingBody, UserRead

log = getLogger(__name__)
# disable_router = True
router = get_router(tags=["user"])


@router.get("", response_model=UserRead, summary="Get current user info")
async def user_info(session: CoreSessionDep, user: Annotated[UserRead, CurrentUserDep]):
    return await _user_read(session, user)


async def _user_read(session: CoreSessionDep, account: Any) -> UserRead:
    """The user as the API shows it: permissions from the provider, the project's profile if it has one."""
    permissions = await resolve_user_permissions(session, account.id)
    profile = await load_profile(session, account.id)
    return UserRead.model_validate(account, from_attributes=True).model_copy(
        update={"permissions": permissions, "profile": profile}
    )


@router.patch("", response_model=UserRead, summary="Update own profile")
async def update_profile(
    body: ProfileUpdate,
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
):
    """Change the first name, the last name or the email of the authenticated user.

    Only the fields that are sent change; an empty ``email`` removes it. Changing the email asks for the current
    password (``400 auth.password_incorrect``); an email that another user has is ``400 user.email_exists``.
    """
    from . import security
    from .cruds import normalize_email

    account = await cruds.get_user_by_id(session, user.id)
    if account is None:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "user.not_found")
    wanted = body.email if "email" in body.model_fields_set else None
    if wanted is not None and normalize_email(wanted) != account.email:
        if security.is_user_locked(account):
            raise security.locked_error(account, ("body", "currentPassword"))
        if not body.current_password or not await security.check_current_password(
            session, account, body.current_password
        ):
            raise api_error(status.HTTP_400_BAD_REQUEST, "auth.password_incorrect", loc=("body", "currentPassword"))
    try:
        account = await cruds.update_user(
            session, account, email=wanted, first_name=body.first_name, last_name=body.last_name
        )
    except MessageError as exc:
        raise HTTPException(status_code=400, detail=detail_from_error(exc, loc=("body", "email"))) from exc
    if body.profile is not None:
        await _save_profile(session, account.id, body.profile)
    return await _user_read(session, account)


async def _save_profile(session: CoreSessionDep, user_id: Any, changes: dict[str, Any]) -> None:
    """Save the ``profile`` of a PATCH: ``422`` for a value the schema refuses or when no profile is registered."""
    if get_profile_config() is None:
        raise api_error(422, "user.profile_not_configured", loc=("body", "profile"))
    try:
        await save_profile(session, user_id, changes)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise RequestValidationError([{**e, "loc": ("body", "profile", *e["loc"])} for e in errors]) from exc


@router.get(
    "/settings/schema",
    summary="Schema UI per il rendering dei settings utente",
)
async def user_settings_schema():
    return await cruds.get_settings_schema()


@router.get("/settings", summary="Get current user settings")
async def user_settings(session: CoreSessionDep, user: Annotated[UserRead, CurrentUserDep]):
    instance = await cruds.get_user_settings(session, user.id)
    return cruds.dump_settings_values(instance)


def _settings_error(exc: MessageError) -> HTTPException:
    """422 for a refused setting: a bad value points at ``body.value``, a bad name at the path."""
    loc = ("body", "value") if exc.code == "user.settings_value_invalid" else ("path", "key")
    return HTTPException(status_code=422, detail=detail_from_error(exc, loc=loc))


@router.put("/settings/{category}/{key}", summary="Update user settings")
async def update_user_settings(
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
    category: str,
    key: str,
    body: SettingBody,
):
    try:
        instance = await cruds.update_user_settings(session, user.id, category, key, body.value)
    except MessageError as exc:
        raise _settings_error(exc) from exc
    return cruds.dump_settings_values(instance)


@router.patch("/settings", summary="Update several user settings at once")
async def update_user_settings_bulk(
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
    changes: Annotated[dict[str, dict[str, SettingValue]], Body()],
):
    """Set many values with ``{section: {key: value}}``; all of them are validated and saved, or none."""
    try:
        instance = await cruds.update_user_settings_bulk(session, user.id, changes)
    except MessageError as exc:
        raise _settings_error(exc) from exc
    return cruds.dump_settings_values(instance)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, skip_envelope=True, summary="Change own password")
async def change_password(
    body: PasswordChange,
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
) -> Response:
    """Change the password of the authenticated user; it asks for the current one.

    Tokens issued before the change stop working (the client must log in again) and a locked-out state is cleared.
    """
    from . import security

    account = await cruds.get_user_by_id(session, user.id)
    if account is not None and security.is_user_locked(account):
        raise security.locked_error(account, ("body", "currentPassword"))
    if account is None or not await security.check_current_password(session, account, body.current_password):
        raise api_error(status.HTTP_400_BAD_REQUEST, "auth.password_incorrect", loc=("body", "currentPassword"))
    if body.new_password == body.current_password:
        raise api_error(422, "auth.password_unchanged", loc=("body", "newPassword"))
    try:
        await cruds.update_user(session, account, password=body.new_password)
    except MessageError as exc:
        raise HTTPException(status_code=422, detail=detail_from_error(exc, loc=("body", "newPassword"))) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
