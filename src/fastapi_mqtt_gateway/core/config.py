from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Application
    app_name: str = "FastAPI MQTT Gateway"
    app_version: str = "0.1.0"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # MQTT Broker
    mqtt_broker_host: str = "mqtt-broker"
    mqtt_broker_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_client_id: str = "fastapi-mqtt-gateway"
    mqtt_keepalive: int = 60
    mqtt_clean_session: bool = True
    mqtt_use_tls: bool = False
    mqtt_ca_certs: str = ""
    mqtt_certfile: str = ""
    mqtt_keyfile: str = ""

    # API credentials are independent of the broker credentials.
    api_username: str = Field(min_length=1, max_length=256)
    api_password: str = Field(min_length=16, max_length=1024, repr=False)

    # No usable defaults: missing credentials must prevent startup.
    jwt_secret_key: str = Field(min_length=32, repr=False)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    jwt_refresh_token_expire_days: int = 7

    # CORS
    cors_allowed_origins: list[str] = Field(default_factory=list)

    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Topic Management
    default_topic_prefix: str = "gateway"
    allowed_topic_patterns: list[str] = Field(default_factory=lambda: ["#"])
    blocked_topic_patterns: list[str] = Field(default_factory=lambda: ["$SYS/#", "$share/#"])

    # Retained Message Query
    retained_query_timeout: float = 5.0

    mqtt_queue_size: int = Field(default=256, ge=1, le=4096)
    websocket_queue_size: int = Field(default=64, ge=1, le=1024)
    websocket_max_topics: int = Field(default=32, ge=1, le=256)
    websocket_auth_timeout: float = Field(default=5.0, gt=0, le=30)
    websocket_send_timeout: float = Field(default=10.0, gt=0, le=60)
    max_message_bytes: int = Field(default=1048576, ge=1, le=1048576)

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    @field_validator("api_username", "api_password", "jwt_secret_key")
    @classmethod
    def reject_placeholder_credentials(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or normalized.startswith(("change-me", "replace-me", "your-")):
            raise ValueError("Configure a non-placeholder API credential or JWT signing secret")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v.upper()

    @field_validator(
        "allowed_topic_patterns", "blocked_topic_patterns", "cors_allowed_origins", mode="before"
    )
    @classmethod
    def parse_patterns(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("allowed_topic_patterns", "blocked_topic_patterns")
    @classmethod
    def validate_acl_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            levels = pattern.split("/")
            if not pattern or "\x00" in pattern or len(pattern.encode()) > 65535:
                raise ValueError("Invalid MQTT ACL filter")
            for index, level in enumerate(levels):
                if ("+" in level and level != "+") or (
                    "#" in level and (level != "#" or index != len(levels) - 1)
                ):
                    raise ValueError("Invalid MQTT ACL wildcard")
        return patterns

    @property
    def mqtt_broker_url(self) -> str:
        scheme = "mqtts" if self.mqtt_use_tls else "mqtt"
        auth = ""
        if self.mqtt_username:
            auth = f"{self.mqtt_username}:{self.mqtt_password}@"
        return f"{scheme}://{auth}{self.mqtt_broker_host}:{self.mqtt_broker_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # BaseSettings loads required fields from the environment.
