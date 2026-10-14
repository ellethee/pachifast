"""MFA sender adapter — hook point for sending OTPs via email and SMS.

To send OTPs: subclass MfaSenderAdapter and
register the subclass with configure_sender() before creating the app.
"""

import logging
import uuid

log = logging.getLogger(__name__)


class MfaSenderAdapter:
    """Base adapter for sending OTPs via email and SMS.

    The default implementation is a no-op with a warning. The host app must
    register its own subclass through configure_sender().
    """

    async def send_email_otp(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        otp_code: str,
        expires_in_minutes: int,
    ) -> None:
        log.warning(
            "MfaSenderAdapter.send_email_otp not implemented — register a custom adapter with configure_sender()."
        )

    async def send_sms_otp(
        self,
        *,
        user_id: uuid.UUID,
        phone_number: str,
        otp_code: str,
        expires_in_minutes: int,
    ) -> None:
        log.warning(
            "MfaSenderAdapter.send_sms_otp not implemented — register a custom adapter with configure_sender()."
        )


_sender_class: "type[MfaSenderAdapter] | None" = None


def configure_sender(cls: "type[MfaSenderAdapter]") -> None:
    """Register a subclass of MfaSenderAdapter as the active sender."""
    global _sender_class
    _sender_class = cls


def get_sender() -> MfaSenderAdapter:
    """Return the active MFA sender (no-op by default)."""
    if _sender_class is not None:
        return _sender_class()
    return MfaSenderAdapter()
