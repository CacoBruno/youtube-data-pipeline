from pathlib import Path

from youtube_pipeline.collectors.transcripts import _language_candidates
from youtube_pipeline.config import load_config


def test_transcription_enabled_with_whisper_and_retry():
    path = Path(__file__).parents[1] / "configs" / "example.yaml"
    cfg = load_config(path)
    assert cfg.transcripts.enabled is True
    assert cfg.transcripts.use_whisper is True
    assert cfg.transcripts.retry_failed is True


def test_language_candidates_expand_base_language():
    assert _language_candidates(["pt-BR", "pt", "en"]) == ["pt-BR", "pt", "en"]
    assert _language_candidates(["es-MX"]) == ["es-MX", "es"]
