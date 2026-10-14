from pachifast.schemas.responses import PageParams


class UserPaginted(PageParams):
    search_fields = ["username", "email", "first_name", "last_name"]
    # never the password hash or the lockout counters
    order_fields = [
        "username",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_superuser",
        "is_staff",
        "created_at",
        "updated_at",
        "last_login_at",
    ]
