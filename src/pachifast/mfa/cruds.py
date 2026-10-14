"""Async DB operations per il modulo MFA."""

import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pachifast.database.utils import utcnow

from .models import UserMfaConfig, UserMfaPendingChallenge


async def get_mfa_config(session: AsyncSession, user_id: uuid.UUID) -> UserMfaConfig | None:
    result = await session.execute(select(UserMfaConfig).where(UserMfaConfig.user_id == user_id))
    return result.scalar_one_or_none()


async def create_or_update_mfa_config(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    method: str,
    totp_secret: str | None = None,
    backup_codes_hashed: list[str],
    phone_number: str | None = None,
    is_enabled: bool = False,
) -> UserMfaConfig:
    existing = await get_mfa_config(session, user_id)
    if existing:
        existing.method = method
        existing.totp_secret = totp_secret
        existing.backup_codes = backup_codes_hashed
        existing.phone_number = phone_number
        existing.is_enabled = is_enabled
        existing.updated_at = utcnow()
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing
    cfg = UserMfaConfig(
        user_id=user_id,
        method=method,
        totp_secret=totp_secret,
        backup_codes=backup_codes_hashed,
        phone_number=phone_number,
        is_enabled=is_enabled,
    )
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg


async def enable_mfa_config(session: AsyncSession, user_id: uuid.UUID) -> UserMfaConfig | None:
    cfg = await get_mfa_config(session, user_id)
    if not cfg:
        return None
    cfg.is_enabled = True
    cfg.updated_at = utcnow()
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg


async def delete_mfa_config(session: AsyncSession, user_id: uuid.UUID) -> bool:
    result = await session.execute(delete(UserMfaConfig).where(UserMfaConfig.user_id == user_id))
    await session.commit()
    return result.rowcount > 0


async def update_backup_codes(
    session: AsyncSession, user_id: uuid.UUID, hashed_codes: list[str]
) -> UserMfaConfig | None:
    cfg = await get_mfa_config(session, user_id)
    if not cfg:
        return None
    cfg.backup_codes = hashed_codes
    cfg.updated_at = utcnow()
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg


async def remove_used_backup_code(session: AsyncSession, cfg: UserMfaConfig, index: int) -> bool:
    """Consume a backup code; ``False`` if somebody else consumed (or changed) the list first.

    Compare-and-set: the row is updated only while its list is still the one the code was checked against, so
    a code works once even when two requests present it at the same time.
    """
    before = list(cfg.backup_codes)
    after = before[:index] + before[index + 1 :]
    result = await session.execute(
        update(UserMfaConfig)
        .where(UserMfaConfig.id == cfg.id, UserMfaConfig.backup_codes == before)
        .values(backup_codes=after, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount == 1


async def create_pending_challenge(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    jti: str,
    method: str,
    otp_hash: str | None,
    expires_at: datetime,
) -> UserMfaPendingChallenge:
    challenge = UserMfaPendingChallenge(
        jti=jti,
        user_id=user_id,
        method=method,
        otp_hash=otp_hash,
        expires_at=expires_at,
    )
    session.add(challenge)
    await session.commit()
    await session.refresh(challenge)
    return challenge


async def get_pending_challenge_by_jti(session: AsyncSession, jti: str) -> UserMfaPendingChallenge | None:
    result = await session.execute(select(UserMfaPendingChallenge).where(UserMfaPendingChallenge.jti == jti))
    return result.scalar_one_or_none()


async def get_latest_challenge(
    session: AsyncSession, user_id: uuid.UUID, method: str
) -> UserMfaPendingChallenge | None:
    """The newest challenge of ``user_id`` for ``method`` (the one a setup just sent), or ``None``."""
    result = await session.execute(
        select(UserMfaPendingChallenge)
        .where(UserMfaPendingChallenge.user_id == user_id, UserMfaPendingChallenge.method == method)
        .order_by(UserMfaPendingChallenge.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def claim_challenge(session: AsyncSession, challenge_id: int) -> bool:
    """Delete the challenge; ``True`` only for the one request that really removed it.

    One ``DELETE`` in the database: two parallel requests with the same correct code cannot both succeed.
    """
    result = await session.execute(delete(UserMfaPendingChallenge).where(UserMfaPendingChallenge.id == challenge_id))
    await session.commit()
    return result.rowcount == 1


async def claim_totp_step(session: AsyncSession, config_id: int, step: int) -> bool:
    """Record ``step`` as the last accepted TOTP step; ``False`` if that step (or a later one) was already used.

    One conditional ``UPDATE`` in the database, so it holds across workers and hosts and a code works once even
    when two requests present it at the same moment.
    """
    result = await session.execute(
        update(UserMfaConfig)
        .where(
            UserMfaConfig.id == config_id,
            or_(UserMfaConfig.last_totp_step.is_(None), UserMfaConfig.last_totp_step < step),
        )
        .values(last_totp_step=step)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount == 1


async def consume_challenge_attempt(session: AsyncSession, challenge_id: int, max_attempts: int) -> bool:
    """Spend one attempt of the challenge *before* the code is checked; ``False`` if none is left.

    A single conditional ``UPDATE``: parallel requests cannot spend more than ``max_attempts`` in total.
    """
    result = await session.execute(
        update(UserMfaPendingChallenge)
        .where(UserMfaPendingChallenge.id == challenge_id, UserMfaPendingChallenge.attempts < max_attempts)
        .values(attempts=UserMfaPendingChallenge.attempts + 1)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount == 1


async def increment_challenge_attempts(session: AsyncSession, challenge_id: int) -> int:
    await session.execute(
        update(UserMfaPendingChallenge)
        .where(UserMfaPendingChallenge.id == challenge_id)
        .values(attempts=UserMfaPendingChallenge.attempts + 1)
    )
    await session.commit()
    result = await session.execute(
        select(UserMfaPendingChallenge.attempts).where(UserMfaPendingChallenge.id == challenge_id)
    )
    return result.scalar_one()


async def delete_pending_challenge(session: AsyncSession, challenge_id: int) -> None:
    await session.execute(delete(UserMfaPendingChallenge).where(UserMfaPendingChallenge.id == challenge_id))
    await session.commit()


async def cleanup_expired_challenges(session: AsyncSession) -> int:
    result = await session.execute(delete(UserMfaPendingChallenge).where(UserMfaPendingChallenge.expires_at < utcnow()))
    await session.commit()
    return result.rowcount
