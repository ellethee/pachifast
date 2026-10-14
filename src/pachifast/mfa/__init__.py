"""MFA module for pachifast: 2FA via TOTP, email OTP, SMS OTP."""

import logging

from pachifast.modules import AppModule, MigrationTarget

from .sender import MfaSenderAdapter, configure_sender, get_sender

log = logging.getLogger(__name__)


async def _create_mfa_tables(app):
    """Create the MFA tables at startup without requiring Alembic."""
    from pachifast.database.engine import get_auth_database_url, get_engine_from_url

    from .models import UserMfaConfig, UserMfaPendingChallenge

    database_url = get_auth_database_url(required=False)
    if not database_url:
        log.warning("MFA: AUTH_DATABASE_URL not configured, MFA tables not created")
        return

    tables = [
        UserMfaConfig.__table__,  # pyright: ignore[reportAttributeAccessIssue]
        UserMfaPendingChallenge.__table__,  # pyright: ignore[reportAttributeAccessIssue]
    ]

    async with get_engine_from_url(database_url).begin() as conn:
        await conn.run_sync(lambda sync_conn: [t.create(sync_conn, checkfirst=True) for t in tables])
    log.info("MFA tables created/verified")


MfaModule = AppModule(
    name="mfa",
    kind="app",
    router_modules=("pachifast.mfa.root",),
    startup_hooks=(_create_mfa_tables,),
    migration_targets=(
        MigrationTarget(
            name="mfa",
            model_modules=("pachifast.mfa.models",),
            database_url_field="AUTH_DATABASE_URL",
        ),
    ),
)

__all__ = ["MfaModule", "MfaSenderAdapter", "configure_sender", "get_sender"]
