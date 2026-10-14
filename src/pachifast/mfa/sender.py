"""MFA sender adapter — hook point per invio OTP via email e SMS.

Per inviare gli OTP: subclassare MfaSenderAdapter e
registrare la sottoclasse con configure_sender() prima di creare l'app.
"""

import logging
import uuid

log = logging.getLogger(__name__)


class MfaSenderAdapter:
    """Adapter base per l'invio di OTP via email e SMS.

    L'implementazione di default è no-op con warning. L'app host deve
    registrare una propria sottoclasse tramite configure_sender().
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
    """Registra una sottoclasse di MfaSenderAdapter come sender attivo."""
    global _sender_class
    _sender_class = cls


def get_sender() -> MfaSenderAdapter:
    """Restituisce il sender MFA attivo (no-op di default)."""
    if _sender_class is not None:
        return _sender_class()
    return MfaSenderAdapter()
