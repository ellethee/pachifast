from project_modules import MODULES

from pachifast import create_app

app = create_app(
    MODULES,
    cors_origins=[
        "http://localhost",
        "http://127.0.0.1:8000",
    ],
)
