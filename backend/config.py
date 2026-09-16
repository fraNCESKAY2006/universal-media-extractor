import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_DIR: str = os.getenv("STORAGE_DIR", "/tmp/ume_storage")
    PROXY_URL: str | None = os.getenv("PROXY_URL", None)

    class Config:
        case_sensitive = True

settings = Settings()
os.makedirs(settings.STORAGE_DIR, exist_ok=True)