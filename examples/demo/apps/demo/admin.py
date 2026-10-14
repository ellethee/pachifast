"""Model admin of the demo: the generic CRUD screens for items and files (see the pachifast guide "Model admin")."""

from pachifast.admin import ModelAdmin, admin_action, register_admin

from .models import DemoFile, DemoItem


@register_admin(DemoItem)
class ItemAdmin(ModelAdmin):
    """Items: searchable by name and notes, filterable by status, category and active flag."""

    name = "item"
    title = "Item"
    list_display = ["name", "category", "status", "price", "quantity", "active", "created_at"]
    search_fields = ["name", "notes"]
    list_filter = ["status", "category", "active"]
    readonly_fields = ["created_at"]
    actions = ["archive", "activate"]

    @admin_action(label="Archive selected")
    async def archive(self, session, rows) -> str:
        """Set the status of the selected items to archived."""
        for row in rows:
            row.status = "archived"
        return f"{len(rows)} archived"

    @admin_action(label="Activate selected", confirm=False)
    async def activate(self, session, rows) -> str:
        """Set the status of the selected items to active."""
        for row in rows:
            row.status = "active"
        return f"{len(rows)} activated"


@register_admin(DemoFile)
class FileAdmin(ModelAdmin):
    """Files: metadata only, read-only (the content is managed by the upload routes)."""

    name = "file"
    title = "File"
    search_fields = ["name"]
    readonly_fields = ["name", "content_type", "size", "created_at"]
