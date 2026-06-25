"""Configuration centralisée, chargée depuis l'environnement (12-factor)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Base de données
    database_url: str = "postgresql+asyncpg://mailarchiver:change-me@postgres:5432/mailarchiver"

    # File de messages
    amqp_url: str = "amqp://mailarchiver:change-me@rabbitmq:5672/"
    raw_mail_queue: str = "raw_mail"

    # Stockage objet (S3 / MinIO)
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "change-me"
    s3_bucket: str = "archives"
    s3_region: str = "us-east-1"

    # Index de recherche
    opensearch_url: str = "http://opensearch:9200"
    opensearch_index: str = "messages"

    # Cryptographie
    master_key_file: str = "/secrets/master.key"
    signing_private_key_file: str = "/secrets/signing_private.pem"
    signing_public_key_file: str = "/secrets/signing_public.pem"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton de configuration."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
