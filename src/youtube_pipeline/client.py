from __future__ import annotations

import os

from dotenv import load_dotenv
from googleapiclient.discovery import build

from .config import ProjectConfig


def build_youtube_client(config: ProjectConfig):
    load_dotenv()
    api_key = os.getenv(config.api.key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Chave do YouTube não encontrada. Defina {config.api.key_env} no ambiente ou em um arquivo .env."
        )
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)
