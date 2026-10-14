"""Built-in reusable core module registry."""

from pachifast import AppModule, MigrationTarget

module = AppModule(
    name="core",
    kind="core",
    router_modules=(
        "pachifast.auth.root",
        "pachifast.auth.user",
        "pachifast.infos.root",
        "pachifast.admin.user",
        "pachifast.admin.permissions",
        "pachifast.health.health",
        "pachifast.version.root",
    ),
    migration_targets=(
        MigrationTarget(
            name="auth",
            database_url_field="AUTH_DATABASE_URL",
            model_modules=(
                "pachifast.auth.models",
                "pachifast.mfa.models",
                "pachifast.audit.models",
                "pachifast.events.models",
            ),
        ),
    ),
    startup_hooks=("pachifast.startup:init_db",),
)
