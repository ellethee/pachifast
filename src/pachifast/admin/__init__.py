"""Administration of the core: users, groups and permissions, and the generic model admin.

The model admin is optional: add :data:`AdminModule` to the project's modules, then declare a model in the
``admin.py`` of an app (``@register_admin(Model)`` on a :class:`ModelAdmin`). See the guide *Model admin*.
"""

from pachifast.modules import AppModule, MigrationTarget

from .registry import ModelAdmin, admin_action, all_admins, register_admin

AdminModule = AppModule(
    name="admin",
    kind="app",
    router_modules=("pachifast.admin.generic",),
    startup_hooks=(
        "pachifast.admin.permissions_sync:create_admin_tables",
        "pachifast.admin.permissions_sync:sync_admin_permissions",
    ),
    migration_targets=(
        MigrationTarget(
            name="admin",
            model_modules=("pachifast.admin.models",),
            database_url_field="AUTH_DATABASE_URL",
        ),
    ),
)

__all__ = ["AdminModule", "ModelAdmin", "admin_action", "all_admins", "register_admin"]
