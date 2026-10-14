"""User management service: the only CLI module touching the auth layer.

Everything here is isolated so it can follow the auth API without touching the commands.
"""

import asyncio
import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pachifast.cli.helpers.project import load_project_modules_if_present, project_context

F = TypeVar("F", bound=Callable[..., Any])


def in_project(func: F) -> F:
    """Run a service function from the project root (its ``base_dir``), imports included.

    The auth modules read the settings (``.env``, YAML) when they are imported, so the import must happen with the
    project as the current directory, whatever directory the command was started from.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, base_dir: str = ".", **kwargs: Any) -> Any:
        root = str(Path(base_dir).resolve())  # resolved before changing directory: base_dir may be relative
        with project_context(root):
            return func(*args, base_dir=root, **kwargs)

    return wrapper  # type: ignore[return-value]


DEFAULT_MIN_PASSWORD_LENGTH = 12


def _min_password_length() -> int:
    """The ``password_min_length`` of the project (the same rule as the API).

    The check runs before the project is fully configured (it must not need a ``SECRET_KEY`` to tell a
    password is too short), so an unreadable configuration falls back to the default.
    """
    from pachifast.config import get_settings

    try:
        return get_settings().password_min_length
    except Exception:
        return DEFAULT_MIN_PASSWORD_LENGTH


def check_password_policy(password: str, *, force: bool = False) -> None:
    """Validate the password against the CLI policy.

    Raises:
        ValueError: If the password is empty, or too short and ``force`` is false.
    """
    if not password:
        raise ValueError("Password cannot be empty.")
    minimum = _min_password_length()
    if not force and len(password) < minimum:
        raise ValueError(f"Password too short (min {minimum} chars). Use --force to bypass.")


def _hash_password(password: str) -> str:
    """Hash a password with the auth layer hasher."""
    from pachifast.auth import security

    for name in ("hash_password", "get_password_hash"):
        hasher = getattr(security, name, None)
        if hasher is not None:
            return hasher(password)
    raise RuntimeError("No password hashing function found in pachifast.auth.security")


async def _with_session(base_dir: str, action: Any) -> Any:
    """Run ``action(session)`` inside a project-configured database session."""
    from pachifast.config import get_settings
    from pachifast.database.engine import get_core_sessionmaker
    from pachifast.modules import configure_modules

    with project_context(base_dir) as root:
        configure_modules(load_project_modules_if_present(root))
        get_settings.cache_clear()
        try:
            sessionmaker = get_core_sessionmaker()
            async with sessionmaker() as session:
                return await action(session)
        finally:
            get_settings.cache_clear()


@in_project
def create_user(
    username: str,
    *,
    base_dir: str = ".",
    password: str,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    is_active: bool = True,
    is_staff: bool = False,
    is_superuser: bool = False,
) -> str:
    """Create a user in the project's auth database and return its username."""
    from pachifast.auth.cruds import create_user as crud_create_user
    from pachifast.auth.schemas import UserCreate

    async def action(session: Any) -> str:
        user = await crud_create_user(
            session,
            UserCreate(
                username=username,
                password=password,
                email=email,
                first_name=first_name,
                last_name=last_name,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser,
            ),
        )
        return user.username

    return asyncio.run(_with_session(base_dir, action))


@in_project
def list_users(filter_str: str | None = None, *, base_dir: str = ".") -> list[dict[str, Any]]:
    """List users, optionally filtered by a substring of username or email."""
    from sqlalchemy.future import select

    from pachifast.auth.models import User

    async def action(session: Any) -> list[dict[str, Any]]:
        users = (await session.execute(select(User).order_by(User.username))).scalars().all()
        if filter_str:
            needle = filter_str.lower()
            users = [u for u in users if needle in u.username.lower() or needle in (u.email or "").lower()]
        return [
            {
                "id": str(u.id),
                "username": u.username,
                "email": u.email or "",
                "is_active": u.is_active,
                "is_staff": u.is_staff,
                "is_superuser": u.is_superuser,
            }
            for u in users
        ]

    return asyncio.run(_with_session(base_dir, action))


@in_project
def update_user(
    username: str,
    *,
    base_dir: str = ".",
    password: str | None = None,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    is_active: bool | None = None,
    is_staff: bool | None = None,
    is_superuser: bool | None = None,
    force: bool = False,
) -> str:
    """Update an existing user and return its username.

    Raises:
        ValueError: If the user is missing or the password violates the policy.
    """
    from sqlalchemy.future import select

    from pachifast.auth.models import User

    if password is not None:
        check_password_policy(password, force=force)

    async def action(session: Any) -> str:
        user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: {username!r}")
        if password is not None:
            _set_password(user, _hash_password(password))
        updates = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "is_active": is_active,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
        }
        for field, value in updates.items():
            if value is not None:
                setattr(user, field, value)
        await session.commit()
        await session.refresh(user)
        return user.username

    return asyncio.run(_with_session(base_dir, action))


@in_project
def delete_user(username: str, *, base_dir: str = ".") -> str:
    """Delete a user (and the data linked to it) and return its username.

    Raises:
        ValueError: If the user does not exist.
    """
    from sqlalchemy.future import select

    from pachifast.auth.cruds import delete_user as crud_delete_user
    from pachifast.auth.models import User

    async def action(session: Any) -> str:
        user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: {username!r}")
        name = user.username
        await crud_delete_user(session, user.id)
        return name

    return asyncio.run(_with_session(base_dir, action))


def _set_password(user: Any, hashed: str) -> None:
    """Store a password hash on the user model, whichever column name it uses."""
    attr = "password_hash" if hasattr(user, "password_hash") else "password"
    setattr(user, attr, hashed)
    # like the admin API: tokens issued before the change stop working and a lockout is lifted
    if hasattr(user, "password_changed_at"):
        from pachifast.database.utils import utcnow

        user.password_changed_at = utcnow()
        user.failed_login_count = 0
        user.locked_until = None


@in_project
def encrypt_mfa_secrets(*, base_dir: str = ".") -> int:
    """Encrypt the TOTP secrets that are still stored as plain text; return how many were rewritten."""

    async def action(session: Any) -> int:
        from sqlalchemy import String, select, type_coerce, update

        from pachifast.database.encrypted import PREFIX
        from pachifast.mfa.models import UserMfaConfig

        raw = type_coerce(UserMfaConfig.totp_secret, String)  # the stored text, as it is in the table
        rows = (
            await session.execute(select(UserMfaConfig.id, raw).where(UserMfaConfig.totp_secret.is_not(None)))
        ).all()
        count = 0
        for config_id, stored in rows:
            if stored and not stored.startswith(PREFIX):
                await session.execute(
                    update(UserMfaConfig).where(UserMfaConfig.id == config_id).values(totp_secret=stored)
                )
                count += 1
        await session.commit()
        return count

    return asyncio.run(_with_session(base_dir, action))
