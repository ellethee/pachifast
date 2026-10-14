import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MfaStatusResponse(BaseModel):
    enabled: bool
    method: str | None = None


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    backup_codes: list[str]


class TotpConfirmRequest(BaseModel):
    code: str


class TotpConfirmResponse(BaseModel):
    ok: bool
    method: Literal["totp"] = "totp"


class OtpConfirmRequest(BaseModel):
    code: str


class EmailConfirmResponse(BaseModel):
    ok: bool
    method: Literal["email"] = "email"


class SmsConfirmResponse(BaseModel):
    ok: bool
    method: Literal["sms"] = "sms"


class EmailSetupResponse(BaseModel):
    ok: bool
    method: Literal["email"] = "email"
    backup_codes: list[str]
    #: the channel is not active until ``POST /mfa/email/confirm`` receives the code that was just sent to it
    confirm_required: bool = True


class SmsSetupRequest(BaseModel):
    #: international format (E.164): ``+`` and 7 to 15 digits; spaces, dots, dashes and brackets are dropped
    phone_number: str = Field(min_length=7, max_length=32)

    @field_validator("phone_number")
    @classmethod
    def _e164(cls, value: str) -> str:
        """Normalize the number and refuse what is not an international number."""
        number = re.sub(r"[\s.\-()]", "", value)
        if not re.fullmatch(r"\+[1-9]\d{6,14}", number):
            raise ValueError("The phone number must be in international format, e.g. +390123456789")
        return number


class SmsSetupResponse(BaseModel):
    ok: bool
    method: Literal["sms"] = "sms"
    backup_codes: list[str]
    #: the number is not active until ``POST /mfa/sms/confirm`` receives the code that was just sent to it
    confirm_required: bool = True


class MfaDisableRequest(BaseModel):
    password: str


class MfaDisableResponse(BaseModel):
    ok: bool


class BackupCodesRegenerateRequest(BaseModel):
    password: str


class BackupCodesRegenerateResponse(BaseModel):
    backup_codes: list[str]


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str
