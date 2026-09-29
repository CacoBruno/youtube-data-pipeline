from pathlib import Path

from youtube_pipeline.collectors import transcripts
from youtube_pipeline.config import load_config


def test_collect_transcript_reuses_injected_whisper_model(monkeypatch):
    path = Path(__file__).parents[1] / "configs" / "example.yaml"
    cfg = load_config(path)
    cfg.transcripts.use_ytdlp = False
    cfg.transcripts.use_whisper = True

    injected_model = object()

    monkeypatch.setattr(
        transcripts,
        "_transcript_api",
        lambda *args, **kwargs: (None, [], {"type": "NoCaption", "message": "test"}),
    )

    def fake_whisper(video_id, config, model=None):
        assert video_id == "video_test"
        assert model is injected_model
        return (
            {
                "video_id": video_id,
                "transcript_status": "success",
                "transcript_method": "faster_whisper_small",
                "transcript_language": "pt",
                "transcript_text": "texto de teste",
                "transcript_error": None,
                "transcript_captured_at": "2026-09-29T00:00:00Z",
            },
            [],
            None,
        )

    monkeypatch.setattr(transcripts, "_whisper", fake_whisper)

    row, segments = transcripts.collect_transcript(
        "video_test",
        cfg,
        whisper_model=injected_model,
    )

    assert row["transcript_status"] == "success"
    assert row["transcript_text"] == "texto de teste"
    assert segments == []
