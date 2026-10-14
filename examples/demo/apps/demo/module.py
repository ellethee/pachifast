from pachifast import AppModule, MigrationTarget

module = AppModule(
    name="demo",
    kind="app",
    router_modules=("apps.demo.items", "apps.demo.lookups", "apps.demo.files", "apps.demo.lab"),
    settings_modules=("apps.demo.settings",),
    locale_package="apps.demo.locale",
    startup_hooks=("apps.demo.seed:seed",),
    migration_targets=(
        MigrationTarget(
            name="demo",
            database_url_resolver="apps.demo.settings:get_database_url",
            model_modules=("apps.demo.models",),
            version_table="demo_alembic_version",
        ),
    ),
)
