from pachifast.database.aliased_models import AliasedBaseModel


class AdmInfoEngineInfo(AliasedBaseModel):
    server_type: str
    scope: str
    host: str | None = None
    dbname: str | None = None
    port: int | None = None


class AdmInfoSessionInfo(AliasedBaseModel):
    server_type: str
    scope: str
    host: str | None = None
    dbname: str | None = None
    port: int | None = None
