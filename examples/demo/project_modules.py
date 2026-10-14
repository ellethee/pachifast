from apps.demo.module import module as demo_module

from pachifast.admin import AdminModule
from pachifast.mfa import MfaModule
from pachifast.presets import core_module

MODULES = (
    core_module,
    MfaModule,
    AdminModule,
    demo_module,
)
