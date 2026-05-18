from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "Navi MVP"
    app_env: str = "development"
    app_debug: bool = False

    account_sid: str = Field(default="", alias="ACCOUNT_SID")
    auth_token: str = Field(default="", alias="AUTH_TOKEN")
    twilio_number: str = Field(default="", alias="TWILIO_NUMBER")

    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    algorithm: str = Field(default="HS256", alias="ALGORITHM")

    # Admin key must be different from SECRET_KEY (see NAVI-SEC-002)
    admin_key: str = Field(default="", alias="ADMIN_SECRET_KEY")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    database_host: str = Field(default="db", alias="DATABASE_HOST")
    database_name: str = Field(default="navimvp", alias="DATABASE_NAME")
    database_user: str = Field(default="navimvp", alias="DATABASE_USER")
    database_password: str = Field(default="navimvppw", alias="DATABASE_PASSWORD")
    database_port: int = Field(default=5432, alias="DATABASE_PORT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
