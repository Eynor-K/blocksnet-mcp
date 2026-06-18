from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()


class MCPSettings(BaseSettings):
    """Runtime settings for the local stdio MCP server."""

    chat_url: str = Field(validation_alias="CHAT_URL")
    api_key: str = Field(validation_alias="API_KEY")
    model: str = Field(default="gpt-4o-mini", validation_alias="MODEL")
    data_dir: Path = Field(default=PROJECT_ROOT / "data", validation_alias="DATA_DIR")
    output_dir: Path = Field(default=PROJECT_ROOT / "outputs", validation_alias="OUTPUT_DIR")
    max_iterations: int = Field(default=10, validation_alias="MAX_ITERATIONS")

    model_config = {
        "populate_by_name": True,
        "env_file": PROJECT_ROOT / ".env",
        "extra": "ignore",
    }

    def model_post_init(self, _) -> None:
        self.data_dir = self.data_dir.expanduser()
        self.output_dir = self.output_dir.expanduser()
        if not self.data_dir.is_absolute():
            self.data_dir = PROJECT_ROOT / self.data_dir
        if not self.output_dir.is_absolute():
            self.output_dir = PROJECT_ROOT / self.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_mcp_settings() -> MCPSettings:
    return MCPSettings()
