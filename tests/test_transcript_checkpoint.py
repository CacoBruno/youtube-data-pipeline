from pathlib import Path

import pandas as pd
import pytest

from youtube_pipeline.config import load_config
from youtube_pipeline.pipeline import _collect_transcripts_with_checkpoints


def test_transcript_checkpoint_survives_interruption(tmp_path, monkeypatch):
    config_path = Path(__file__).parents[1] / "configs" / "example.yaml"
    cfg = load_config(config_path)
    cfg.name = "checkpoint_test"
    cfg.output.base_dir = str(tmp_path)
    cfg.output.formats = ["csv"]

    def fake_collect_transcript(video_id, config):
        if video_id == "video-2":
            raise KeyboardInterrupt()

        return (
            {
                "video_id": video_id,
                "transcript_status": "success",
                "transcript_method": "fake_method",
                "transcript_language": "pt",
                "transcript_text": "texto de teste",
                "transcript_error": None,
                "transcript_captured_at": "2026-09-21T00:00:00Z",
            },
            [
                {
                    "video_id": video_id,
                    "segment_index": 0,
                    "start_seconds": 0.0,
                    "duration_seconds": 1.0,
                    "text": "texto de teste",
                    "source_method": "fake_method",
                }
            ],
        )

    monkeypatch.setattr(
        "youtube_pipeline.pipeline.collect_transcript",
        fake_collect_transcript,
    )

    with pytest.raises(KeyboardInterrupt):
        _collect_transcripts_with_checkpoints(
            cfg,
            ["video-1", "video-2"],
            pd.DataFrame(),
            pd.DataFrame(),
        )

    transcripts_path = (
        tmp_path
        / "checkpoint_test"
        / "intermediate"
        / "transcripts.csv"
    )
    segments_path = (
        tmp_path
        / "checkpoint_test"
        / "intermediate"
        / "transcript_segments.csv"
    )

    saved_transcripts = pd.read_csv(transcripts_path)
    saved_segments = pd.read_csv(segments_path)

    assert saved_transcripts["video_id"].tolist() == ["video-1"]
    assert saved_transcripts["transcript_status"].tolist() == ["success"]
    assert saved_segments["video_id"].tolist() == ["video-1"]
