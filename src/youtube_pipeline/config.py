from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ApiConfig:
    key_env: str = "YOUTUBE_API_KEY"
    sleep_seconds: float = 0.25
    stop_on_quota: bool = True


@dataclass
class SearchConfig:
    themes: dict[str, list[str]] = field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None
    window_days: int = 30
    orders: list[str] = field(default_factory=lambda: ["relevance"])
    max_pages_per_query: int = 1
    max_results_per_page: int = 50
    region_code: str | None = "BR"
    relevance_language: str | None = "pt"


@dataclass
class TranscriptConfig:
    enabled: bool = True
    languages: list[str] = field(default_factory=lambda: ["pt-BR", "pt", "en"])
    use_ytdlp: bool = True
    use_whisper: bool = True
    retry_failed: bool = True
    use_any_available_caption: bool = True
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None
    whisper_vad_filter: bool = True
    yt_dlp_cookies_from_browser: str | None = None


@dataclass
class CommentsConfig:
    enabled: bool = True
    include_replies: bool = True
    max_per_video: int = 500
    order: str = "relevance"


@dataclass
class OutputConfig:
    base_dir: str = "data"
    formats: list[str] = field(default_factory=lambda: ["parquet"])
    resume: bool = True


@dataclass
class ProjectConfig:
    name: str
    api: ApiConfig
    search: SearchConfig
    transcripts: TranscriptConfig
    comments: CommentsConfig
    output: OutputConfig


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"A seção '{name}' deve ser um objeto YAML.")
    return value


def load_config(path: str | Path) -> ProjectConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    project = _section(raw, "project")
    name = str(project.get("name", "")).strip()
    if not name:
        raise ValueError("project.name é obrigatório.")

    search_raw = _section(raw, "search")
    themes = search_raw.get("themes", {})
    if not isinstance(themes, dict) or not themes:
        raise ValueError("search.themes deve conter ao menos um tema com uma lista de queries.")
    for theme, queries in themes.items():
        if not isinstance(queries, list) or not [q for q in queries if str(q).strip()]:
            raise ValueError(f"Tema '{theme}' precisa ter ao menos uma query.")

    cfg = ProjectConfig(
        name=name,
        api=ApiConfig(**_section(raw, "api")),
        search=SearchConfig(**search_raw),
        transcripts=TranscriptConfig(**_section(raw, "transcripts")),
        comments=CommentsConfig(**_section(raw, "comments")),
        output=OutputConfig(**_section(raw, "output")),
    )

    valid_orders = {"date", "rating", "relevance", "title", "viewCount"}
    invalid_orders = [x for x in cfg.search.orders if x not in valid_orders]
    if invalid_orders:
        raise ValueError(f"search.orders inválido(s): {invalid_orders}")
    if cfg.search.window_days <= 0:
        raise ValueError("search.window_days deve ser > 0.")

    if cfg.search.max_pages_per_query <= 0:
        raise ValueError("search.max_pages_per_query deve ser > 0.")

    if not 1 <= cfg.search.max_results_per_page <= 50:
        raise ValueError("search.max_results_per_page deve estar entre 1 e 50.")
    
    if cfg.comments.max_per_video <= 0:
        raise ValueError("comments.max_per_video deve ser > 0.")
    if cfg.comments.order not in {"relevance", "time"}:
        raise ValueError("comments.order deve ser relevance ou time.")

    formats = {x.lower() for x in cfg.output.formats}
    invalid_formats = formats - {"parquet", "csv"}
    if invalid_formats:
        raise ValueError(f"output.formats inválido(s): {sorted(invalid_formats)}")
    cfg.output.formats = sorted(formats)

    return cfg
