# pyright: reportArgumentType=none
import copy
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from starlette.concurrency import run_in_threadpool

from pachifast import signals
from pachifast.database.utils import utcnow
from pachifast.i18n import MessageError

from .models import (
    Group,
    GroupPermission,
    Permission,
    RefreshTokenRecord,
    User,
    UserGroup,
    UserPermission,
    UserSettingsTable,
)
from .schemas import UserCreate
from .user_settings_discover import build_ui_schema, get_user_settings_model

UserSettingsModel = get_user_settings_model()

SettingValue = str | int | float | bool | None


def normalize_username(username: str) -> str:
    """Normalizza lo username: spazi rimossi e minuscolo."""
    return username.strip().lower()


def normalize_email(email: str | None) -> str | None:
    """Normalizza l'email (minuscolo, senza spazi); stringa vuota -> None."""
    if email is None:
        return None
    email = email.strip().lower()
    return email or None


def to_user_id(user_id: "uuid.UUID | str") -> uuid.UUID | None:
    """Converte un valore in UUID; ritorna None se non e' un UUID valido."""
    if isinstance(user_id, uuid.UUID):
        return user_id
    try:
        return uuid.UUID(str(user_id))
    except ValueError:
        return None


async def get_users(session: AsyncSession) -> list[User]:
    """Elenco di tutti gli utenti."""
    result = await session.execute(select(User))
    return list(result.scalars().all())


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Cerca un utente per username (case-insensitive)."""
    result = await session.execute(select(User).where(User.username == normalize_username(username)))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: "uuid.UUID | str") -> User | None:
    """Cerca un utente per id (UUID o sua rappresentazione stringa)."""
    uid = to_user_id(user_id)
    if uid is None:
        return None
    result = await session.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


async def get_user_permissions(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    q_group = (
        select(Permission.codename)
        .join(GroupPermission, Permission.id == GroupPermission.permission_id)
        .join(UserGroup, GroupPermission.group_id == UserGroup.group_id)
        .where(UserGroup.user_id == user_id)
    )
    q_direct = (
        select(Permission.codename)
        .join(UserPermission, Permission.id == UserPermission.permission_id)
        .where(UserPermission.user_id == user_id)
    )
    g = (await session.execute(q_group)).scalars().all()
    d = (await session.execute(q_direct)).scalars().all()
    return list(set(g) | set(d))


# --- Permission CRUD ---


async def list_permissions(session: AsyncSession) -> list[Permission]:
    result = await session.execute(select(Permission).order_by(Permission.codename))
    return list(result.scalars().all())


async def create_permission(session: AsyncSession, codename: str, description: str = "") -> Permission:
    perm = Permission(codename=codename, description=description)
    session.add(perm)
    await session.commit()
    await session.refresh(perm)
    return perm


async def update_permission(session: AsyncSession, perm_id: int, codename: str, description: str) -> Permission | None:
    perm = await session.get(Permission, perm_id)
    if not perm:
        return None
    perm.codename = codename
    perm.description = description
    await session.commit()
    await session.refresh(perm)
    return perm


async def delete_permission(session: AsyncSession, perm_id: int) -> bool:
    perm = await session.get(Permission, perm_id)
    if not perm:
        return False
    await session.delete(perm)
    await session.commit()
    return True


# --- Group CRUD ---


async def list_groups(session: AsyncSession) -> list[dict]:
    groups = (await session.execute(select(Group).order_by(Group.name))).scalars().all()
    result = []
    for g in groups:
        perms_q = (
            select(Permission)
            .join(GroupPermission, Permission.id == GroupPermission.permission_id)
            .where(GroupPermission.group_id == g.id)
        )
        perms = (await session.execute(perms_q)).scalars().all()
        result.append({"id": g.id, "name": g.name, "description": g.description, "permissions": perms})
    return result


async def create_group(session: AsyncSession, name: str, description: str, permission_ids: list[int]) -> dict:
    group = Group(name=name, description=description)
    session.add(group)
    await session.flush()
    for pid in permission_ids:
        session.add(GroupPermission(group_id=group.id, permission_id=pid))
    await session.commit()
    await session.refresh(group)
    perms = (
        (
            await session.execute(
                select(Permission)
                .join(GroupPermission, Permission.id == GroupPermission.permission_id)
                .where(GroupPermission.group_id == group.id)
            )
        )
        .scalars()
        .all()
    )
    return {"id": group.id, "name": group.name, "description": group.description, "permissions": perms}


async def update_group(
    session: AsyncSession, group_id: int, name: str, description: str, permission_ids: list[int]
) -> dict | None:
    group = await session.get(Group, group_id)
    if not group:
        return None
    group.name = name
    group.description = description
    await session.execute(
        GroupPermission.__table__.delete().where(GroupPermission.group_id == group_id)  # type: ignore
    )
    for pid in permission_ids:
        session.add(GroupPermission(group_id=group_id, permission_id=pid))
    await session.commit()
    await session.refresh(group)
    perms = (
        (
            await session.execute(
                select(Permission)
                .join(GroupPermission, Permission.id == GroupPermission.permission_id)
                .where(GroupPermission.group_id == group_id)
            )
        )
        .scalars()
        .all()
    )
    return {"id": group.id, "name": group.name, "description": group.description, "permissions": perms}


async def delete_group(session: AsyncSession, group_id: int) -> bool:
    group = await session.get(Group, group_id)
    if not group:
        return False
    await session.delete(group)
    await session.commit()
    return True


# --- User↔Group / User↔Permission assignment ---


async def get_user_groups(session: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    q = select(Group).join(UserGroup, Group.id == UserGroup.group_id).where(UserGroup.user_id == user_id)
    groups = (await session.execute(q)).scalars().all()
    result = []
    for g in groups:
        perms_q = (
            select(Permission)
            .join(GroupPermission, Permission.id == GroupPermission.permission_id)
            .where(GroupPermission.group_id == g.id)
        )
        perms = (await session.execute(perms_q)).scalars().all()
        result.append({"id": g.id, "name": g.name, "description": g.description, "permissions": perms})
    return result


async def set_user_groups(session: AsyncSession, user_id: uuid.UUID, group_ids: list[int]) -> list[dict]:
    await session.execute(
        UserGroup.__table__.delete().where(UserGroup.user_id == user_id)  # type: ignore
    )
    for gid in group_ids:
        session.add(UserGroup(user_id=user_id, group_id=gid))
    await session.commit()
    return await get_user_groups(session, user_id)


async def get_user_direct_permissions(session: AsyncSession, user_id: uuid.UUID) -> list[Permission]:
    q = (
        select(Permission)
        .join(UserPermission, Permission.id == UserPermission.permission_id)
        .where(UserPermission.user_id == user_id)
    )
    return list((await session.execute(q)).scalars().all())


async def set_user_direct_permissions(
    session: AsyncSession, user_id: uuid.UUID, permission_ids: list[int]
) -> list[Permission]:
    await session.execute(
        UserPermission.__table__.delete().where(UserPermission.user_id == user_id)  # type: ignore
    )
    for pid in permission_ids:
        session.add(UserPermission(user_id=user_id, permission_id=pid))
    await session.commit()
    return await get_user_direct_permissions(session, user_id)


async def get_user_settings(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> object:
    """Carica i settings utente dal DB e li deserializza nel modello dinamico.

    Se la riga non esiste restituisce il modello con tutti i default.
    I computed_field (es. route, title, icon) sono calcolati automaticamente e non vengono letti dal DB.
    """
    result = await session.execute(select(UserSettingsTable).where(UserSettingsTable.user_id == user_id))
    row = result.scalar_one_or_none()
    data = row.json_settings if row else {}
    return _validate_stored(data)


def _validate_stored(data: dict) -> object:
    """Build the settings from the stored JSON; a value that no longer passes (a limit that changed, an option that
    was removed) falls back to the default instead of breaking the whole document."""
    data = copy.deepcopy(data)
    for _ in range(len(data) * 8 + 8):
        try:
            return UserSettingsModel.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors():
                node: Any = data
                *parents, last = error["loc"]
                for part in parents:
                    node = node.get(part) if isinstance(node, dict) else None
                if isinstance(node, dict):
                    node.pop(last, None)
    return UserSettingsModel()


async def update_user_settings(
    session: AsyncSession,
    user_id: uuid.UUID,
    category: str,
    key: str,
    value: SettingValue,
) -> object:
    """Aggiorna un singolo setting utente e salva nel DB.

    Carica i settings esistenti, aggiorna il valore specificato, e salva tutto come JSON.
    I computed_field (es. route, title, icon) sono esclusi dalla serializzazione e non vengono salvati nel DB.
    Il valore e' validato contro il campo (tipo, ``Literal``, ``ge``/``le``...): se non passa solleva
    ``user.settings_value_invalid`` e nulla viene salvato.
    """
    return await update_user_settings_bulk(session, user_id, {category: {key: value}})


async def update_user_settings_bulk(
    session: AsyncSession,
    user_id: uuid.UUID,
    changes: dict[str, dict[str, SettingValue]],
) -> object:
    """Applica piu' valori (``{sezione: {chiave: valore}}``) con un solo salvataggio: o tutti o nessuno."""
    settings = await get_user_settings(session, user_id)
    for category, values in changes.items():
        section = getattr(settings, category, None)
        if section is None or category not in type(settings).model_fields:
            raise MessageError("user.settings_category_invalid", category=category)
        for key, value in values.items():
            name = _resolve_setting_key(section, category, key)
            try:
                setattr(section, name, value)
            except ValidationError as exc:
                raise MessageError("user.settings_value_invalid", category=category, key=key) from exc
    await save_user_settings(session, user_id, settings)
    return settings


def _resolve_setting_key(section: object, category: str, key: str) -> str:
    """The field name of ``key``, given as the field name (snake_case) or its camelCase alias (as in the schema)."""
    for name, field in type(section).model_fields.items():  # type: ignore[attr-defined]
        if key in (name, field.alias):
            return name
    raise MessageError("user.settings_key_invalid", category=category, key=key)


def dump_settings_values(instance: object) -> dict:
    """Serializza solo i valori dei settings, escludendo computed_field a tutti i livelli.

    Esclude sia i computed_field del modello top-level sia quelli delle sezioni annidate
    (es. route, title, icon definiti come @computed_field in BaseUserSettings).
    """
    model_cls = type(instance)
    # Computed field del modello radice (di solito vuoto per UserSettings)
    top_exclude: set[str] = set(model_cls.model_computed_fields)  # type: ignore[union-attr]
    # Computed field delle sezioni annidate
    nested_exclude: dict[str, set[str]] = {}
    for section_name, field_info in model_cls.model_fields.items():  # type: ignore[union-attr]
        section_cls = field_info.annotation
        if hasattr(section_cls, "model_computed_fields"):
            nested_exclude[section_name] = set(section_cls.model_computed_fields)
    exclude: dict[str, object] = {k: True for k in top_exclude}
    exclude.update(nested_exclude)
    return instance.model_dump(mode="json", exclude=exclude)  # type: ignore[union-attr]


async def save_user_settings(
    session: AsyncSession,
    user_id: uuid.UUID,
    instance: object,
) -> None:
    """Salva i settings utente nel DB escludendo i computed_field a tutti i livelli."""
    data = dump_settings_values(instance)

    result = await session.execute(select(UserSettingsTable).where(UserSettingsTable.user_id == user_id))
    row = result.scalar_one_or_none()
    if row:
        row.json_settings = data
    else:
        session.add(UserSettingsTable(user_id=user_id, json_settings=data))
    await session.commit()


async def create_user(session: AsyncSession, user_in: UserCreate, *, check_password_policy: bool = True) -> User:
    """Crea un utente con password hashata Argon2id.

    Args:
        session: sessione DB.
        user_in: dati del nuovo utente.
        check_password_policy: se False salta la lunghezza minima (uso CLI ``--force``).

    Raises:
        ValueError: password non conforme alla policy oppure username/email gia' esistenti.
    """
    from .security import hash_password, validate_password_policy  # lazy import

    if check_password_policy:
        validate_password_policy(user_in.password)

    user = User(
        username=normalize_username(user_in.username),
        email=normalize_email(user_in.email),
        password_hash=await run_in_threadpool(hash_password, user_in.password),
        first_name=user_in.first_name or "",
        last_name=user_in.last_name or "",
        is_active=user_in.is_active if user_in.is_active is not None else True,
        is_staff=bool(user_in.is_staff),
        is_superuser=bool(user_in.is_superuser),
        password_changed_at=utcnow(),
    )

    session.add(user)
    try:
        await session.commit()
        await session.refresh(user)
    except IntegrityError:
        await session.rollback()
        raise MessageError("user.username_or_email_exists") from None

    await signals.emit(signals.USER_CREATED, user=user)
    return user


async def update_user(
    session: AsyncSession,
    user: User,
    *,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    is_active: bool | None = None,
    is_staff: bool | None = None,
    is_superuser: bool | None = None,
    password: str | None = None,
    check_password_policy: bool = True,
) -> User:
    """Aggiorna i campi indicati (quelli ``None`` restano invariati).

    Un'email vuota ("") azzera l'email. Se cambia la password aggiorna ``password_changed_at``
    e sblocca l'account.

    Raises:
        ValueError: password non conforme oppure email gia' in uso.
    """
    from .security import hash_password, validate_password_policy  # lazy import

    if email is not None:
        user.email = normalize_email(email)
    if first_name is not None:
        user.first_name = first_name
    if last_name is not None:
        user.last_name = last_name
    if is_active is not None:
        user.is_active = is_active
    if is_staff is not None:
        user.is_staff = is_staff
    if is_superuser is not None:
        user.is_superuser = is_superuser
    if password is not None:
        if check_password_policy:
            validate_password_policy(password)
        user.password_hash = await run_in_threadpool(hash_password, password)
        user.password_changed_at = utcnow()
        user.failed_login_count = 0
        user.locked_until = None
    user.updated_at = utcnow()
    session.add(user)
    try:
        await session.commit()
        await session.refresh(user)
    except IntegrityError:
        await session.rollback()
        raise MessageError("user.email_exists") from None
    if password is not None or is_active is False:
        # a new password or a deactivation ends every login: the refresh tokens go with them
        await session.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user.id))
        await session.commit()
    if password is not None:
        await signals.emit(signals.PASSWORD_CHANGED, user=user)
    return user


async def _table_exists(session: AsyncSession, table_name: str) -> bool:
    """True se la tabella esiste nel database della sessione."""
    from sqlalchemy import inspect

    connection = await session.connection()
    return await connection.run_sync(lambda sync_conn: inspect(sync_conn).has_table(table_name))


async def delete_user(session: AsyncSession, user_id: "uuid.UUID | str") -> bool:
    """Elimina un utente (le tabelle collegate cadono in cascata). False se non esiste."""
    user = await get_user_by_id(session, user_id)
    if user is None:
        return False
    # Cancellazione esplicita dei dati collegati: non dipende dal supporto FK del database (es. SQLite).
    # Le tabelle MFA non sono nelle migrazioni del core: se mancano (MFA mai attivato) non c'e' nulla da cancellare.
    from pachifast.mfa.models import UserMfaConfig, UserMfaPendingChallenge

    for model in (UserGroup, UserPermission, UserSettingsTable):
        await session.execute(model.__table__.delete().where(model.user_id == user.id))  # type: ignore
    for model in (UserMfaConfig, UserMfaPendingChallenge):
        if await _table_exists(session, model.__tablename__):  # type: ignore[attr-defined]
            await session.execute(model.__table__.delete().where(model.user_id == user.id))  # type: ignore
    await session.delete(user)
    await session.commit()
    return True


async def get_settings_schema():
    return build_ui_schema(UserSettingsModel)


async def get_first_superuser(session: AsyncSession) -> User | None:
    """Ritorna il primo superuser attivo trovato nel DB."""
    result = await session.execute(select(User).where(User.is_superuser.is_(True), User.is_active.is_(True)).limit(1))
    return result.scalar_one_or_none()
