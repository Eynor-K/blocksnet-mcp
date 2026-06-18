from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _find_project_root() -> Path:
    for path in [Path.cwd(), *Path.cwd().parents]:
        if (path / "data").exists() or (path / ".env").exists():
            return path
    return Path.cwd()


PROJECT_ROOT = _find_project_root()


class Settings(BaseSettings):
    chat_url: str = Field(validation_alias="CHAT_URL")
    api_key: str = Field(validation_alias="API_KEY")
    model: str = Field(default="gpt-4o-mini", validation_alias="MODEL")
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    output_dir: Path = Field(default=PROJECT_ROOT / "outputs")

    model_config = {"populate_by_name": True, "env_file": PROJECT_ROOT / ".env", "extra": "ignore"}

    def model_post_init(self, _) -> None:
        self.output_dir.mkdir(exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Кэшированный синглтон настроек, читается из окружения / .env."""
    return Settings()
