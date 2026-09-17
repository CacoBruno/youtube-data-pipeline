from pathlib import Path

from youtube_pipeline.config import load_config


def test_example_config_loads():
    path = Path(__file__).parents[1] / "configs" / "example.yaml"
    cfg = load_config(path)
    assert cfg.name == "credito_endividamento"
    assert "credito_endividamento" in cfg.search.themes
    assert cfg.comments.include_replies is True
