"""Per-user settings of the demo app: the section "billing" shown in the settings page."""

from typing import ClassVar, Literal

from pachifast.auth.user_settings_discover import BaseUserSettings, ui_field


class BillingSettings(BaseUserSettings):
    """Example of an app section: a ``Literal`` becomes a select, ``ge``/``le`` become the limits of a number."""

    __section_name__ = "billing"
    _title: ClassVar[str] = "Billing"
    _icon: ClassVar[str] = "receipt_long"

    invoice_layout: Literal["classic", "compact", "detailed"] = ui_field("classic", label="Invoice layout")
    payment_terms_days: int = ui_field(30, ge=0, le=120, label="Payment terms (days)")
