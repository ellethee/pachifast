from pachifast.database import AliasedBaseModel


class HealthCheck(AliasedBaseModel):
    name: str
    version: str
    description: str
    database: bool


class LiveStatus(AliasedBaseModel):
    status: str
