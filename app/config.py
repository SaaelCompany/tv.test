from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Lumina"
    secret_key: str = ""
    debug: bool = False

    # 3x-ui
    xui_mock_mode: bool = False
    xui_url: str = ""
    xui_username: str = ""
    xui_password: str = ""
    xui_api_token: str = ""
    xui_inbound_id: int = 1
    xui_sub_base_url: str = ""
    xui_sync_interval: int = 120

    # Robokassa
    robokassa_mock: bool = False
    robokassa_login: str = ""
    robokassa_password1: str = ""
    robokassa_password2: str = ""
    robokassa_test_mode: bool = False
    robokassa_hash_type: str = "md5"
    robokassa_fiscal_mode: bool = False

    # Сайт
    base_url: str = "https://localhost"
    cookie_secure: bool = True
    cookie_domain: str = ""
    admin_password: str = ""
    trusted_hosts: str = ""

    # БД
    database_url: str = "sqlite:///./vpn_shop.db"

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        # Разрешаем пустое значение для локальной разработки,
        # но требуем длину, если ключ задан.
        if v and len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 chars or empty for dev")
        return v

    @field_validator("xui_url", "base_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


settings = Settings()