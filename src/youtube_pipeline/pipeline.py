from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .client import build_youtube_client
from .collectors.channels import collect_channels
from .collectors.comments import collect_comments
from .collectors.transcripts import collect_transcript
from .collectors.videos import collect_videos
from .config import ProjectConfig, load_config
from .discovery.search import discover_videos
from .storage import read_table, upsert, write_table
from .utils import utc_now_iso

ALL_STAGES = ["discovery", "videos", "channels", "transcripts", "comments"]


def _root(cfg: ProjectConfig) -> Path:
    return Path(cfg.output.base_dir) / cfg.name


def _table_path(cfg: ProjectConfig, kind: str, name: str) -> Path:
    return _root(cfg) / kind / name


def _load_processed(cfg: ProjectConfig, name: str) -> pd.DataFrame:
    return read_table(_table_path(cfg, "processed", name))


def _merge_transcripts_into_videos(videos: pd.DataFrame, transcripts: pd.DataFrame) -> pd.DataFrame:
    transcript_cols = [
        "video_id",
        "transcript_status",
        "transcript_method",
        "transcript_language",
        "transcript_text",
        "transcript_error",
        "transcript_captured_at",
    ]
    transcript_cols = [c for c in transcript_cols if c in transcripts.columns]
    old_cols = [c for c in transcript_cols if c != "video_id" and c in videos.columns]
    base = videos.drop(columns=old_cols, errors="ignore")
    if transcripts.empty or "video_id" not in transcripts.columns:
        return base
    return base.merge(transcripts[transcript_cols].drop_duplicates("video_id", keep="last"), on="video_id", how="left")


def _collect_transcripts_with_checkpoints(
    cfg: ProjectConfig,
    video_ids: list[str],
    transcripts: pd.DataFrame,
    segments: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Coleta e persiste cada transcrição antes de avançar para o próximo vídeo."""
    successes = 0
    failures = 0
    methods: dict[str, int] = {}
    processed = 0
    total = len(video_ids)

    for i, video_id in enumerate(video_ids, start=1):
        print(f"    transcript {i}/{total} | video_id={video_id}")
        row, row_segments = collect_transcript(video_id, cfg)
        print(f"      -> {row.get('transcript_status')} | {row.get('transcript_method')}")

        new_row = pd.DataFrame([row])
        transcripts = upsert(transcripts, new_row, ["video_id"])
        write_table(
            transcripts,
            _table_path(cfg, "intermediate", "transcripts"),
            cfg.output.formats,
        )

        new_segments = pd.DataFrame(row_segments)
        if not new_segments.empty:
            segments = upsert(
                segments,
                new_segments,
                ["video_id", "segment_index", "source_method"],
            )
            write_table(
                segments,
                _table_path(cfg, "intermediate", "transcript_segments"),
                cfg.output.formats,
            )

        processed += 1
        status = str(row.get("transcript_status") or "")
        method = str(row.get("transcript_method") or "")

        if status == "success":
            successes += 1
        elif status == "failed":
            failures += 1

        if method:
            methods[method] = methods.get(method, 0) + 1

    return transcripts, segments, {
        "new_rows": processed,
        "successes": successes,
        "failures": failures,
        "methods": methods,
    }


def run_pipeline(config_path: str | Path, stages: list[str] | None = None) -> dict:
    cfg = load_config(config_path)
    selected = stages or ALL_STAGES
    unknown = [x for x in selected if x not in ALL_STAGES]
    if unknown:
        raise ValueError(f"Stage(s) inválido(s): {unknown}. Opções: {ALL_STAGES}")

    root = _root(cfg)
    (root / "processed").mkdir(parents=True, exist_ok=True)
    (root / "intermediate").mkdir(parents=True, exist_ok=True)
    (root / "audit" / "runs").mkdir(parents=True, exist_ok=True)

    youtube = build_youtube_client(cfg)
    started_at = utc_now_iso()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict = {"run_id": run_id, "project": cfg.name, "started_at": started_at, "stages": {}, "config": str(config_path)}

    # DISCOVERY
    print(f"[1/5] discovery | projeto={cfg.name}")
    hits = _load_processed(cfg, "search_hits")
    if "discovery" in selected:
        new_hits = discover_videos(youtube, cfg)
        hits = upsert(hits, new_hits, ["search_id", "video_id", "result_position"])
        write_table(hits, _table_path(cfg, "processed", "search_hits"), cfg.output.formats)
        report["stages"]["discovery"] = {"new_rows": len(new_hits), "total_rows": len(hits), "unique_videos": int(hits["video_id"].nunique()) if not hits.empty else 0}
    if hits.empty:
        raise RuntimeError("Nenhum search_hit disponível. Execute o stage discovery primeiro.")

    video_ids = hits["video_id"].dropna().astype(str).drop_duplicates().tolist()

    # VIDEOS
    print(f"[2/5] videos | ids descobertos={len(video_ids)}")
    videos = _load_processed(cfg, "videos")
    if "videos" in selected:
        known = set(videos["video_id"].astype(str)) if cfg.output.resume and not videos.empty and "video_id" in videos.columns else set()
        todo = [x for x in video_ids if x not in known]
        new_videos = collect_videos(youtube, todo, cfg) if todo else pd.DataFrame()
        videos = upsert(videos, new_videos, ["video_id"])
        write_table(videos, _table_path(cfg, "processed", "videos"), cfg.output.formats)
        report["stages"]["videos"] = {"requested": len(todo), "new_rows": len(new_videos), "total_rows": len(videos)}
    if videos.empty:
        raise RuntimeError("Nenhum vídeo enriquecido disponível. Execute o stage videos primeiro.")

    # CHANNELS
    print("[3/5] channels")
    if "channels" in selected:
        channels = _load_processed(cfg, "channels")
        channel_ids = videos["channel_id"].dropna().astype(str).drop_duplicates().tolist()
        known = set(channels["channel_id"].astype(str)) if cfg.output.resume and not channels.empty and "channel_id" in channels.columns else set()
        todo = [x for x in channel_ids if x not in known]
        new_channels = collect_channels(youtube, todo, cfg) if todo else pd.DataFrame()
        channels = upsert(channels, new_channels, ["channel_id"])
        write_table(channels, _table_path(cfg, "processed", "channels"), cfg.output.formats)
        report["stages"]["channels"] = {"requested": len(todo), "new_rows": len(new_channels), "total_rows": len(channels)}

    # TRANSCRIPTS
    print("[4/5] transcripts")
    transcripts = read_table(_table_path(cfg, "intermediate", "transcripts"))
    if "transcripts" in selected and cfg.transcripts.enabled:
        known: set[str] = set()
        if cfg.output.resume and not transcripts.empty and "video_id" in transcripts.columns:
            if cfg.transcripts.retry_failed and "transcript_status" in transcripts.columns:
                known = set(
                    transcripts.loc[transcripts["transcript_status"].astype(str).eq("success"), "video_id"].astype(str)
                )
            else:
                known = set(transcripts["video_id"].astype(str))

        todo = [
            x
            for x in videos["video_id"].dropna().astype(str).tolist()
            if x not in known
        ]

        segments = read_table(_table_path(cfg, "intermediate", "transcript_segments"))
        checkpoint_stats = {
            "new_rows": 0,
            "successes": 0,
            "failures": 0,
            "methods": {},
        }

        if todo:
            transcripts, segments, checkpoint_stats = _collect_transcripts_with_checkpoints(
                cfg,
                todo,
                transcripts,
                segments,
            )

        videos = _merge_transcripts_into_videos(videos, transcripts)
        write_table(videos, _table_path(cfg, "processed", "videos"), cfg.output.formats)

        report["stages"]["transcripts"] = {
            "requested": len(todo),
            "new_rows": checkpoint_stats["new_rows"],
            "successes": checkpoint_stats["successes"],
            "failures": checkpoint_stats["failures"],
            "methods": checkpoint_stats["methods"],
            "total_rows": len(transcripts),
        }

    # COMMENTS
    print("[5/5] comments")
    if "comments" in selected and cfg.comments.enabled:
        comments = _load_processed(cfg, "comments")
        status = read_table(_table_path(cfg, "intermediate", "comments_status"))
        completed_statuses = {"completed", "no_comments", "comments_disabled", "video_not_found"}
        known = set()
        if cfg.output.resume and not status.empty and {"video_id", "status"}.issubset(status.columns):
            known = set(status.loc[status["status"].isin(completed_statuses), "video_id"].astype(str))
        todo = [x for x in videos["video_id"].dropna().astype(str).tolist() if x not in known]
        new_comments, new_status = collect_comments(youtube, todo, cfg) if todo else (pd.DataFrame(), pd.DataFrame())
        comments = upsert(comments, new_comments, ["comment_id"])
        status = upsert(status, new_status, ["video_id"])
        write_table(comments, _table_path(cfg, "processed", "comments"), cfg.output.formats)
        write_table(status, _table_path(cfg, "intermediate", "comments_status"), cfg.output.formats)
        report["stages"]["comments"] = {"requested_videos": len(todo), "new_rows": len(new_comments), "total_rows": len(comments)}

    report["ended_at"] = utc_now_iso()
    report["outputs"] = {
        "root": str(root),
        "channels": str(_table_path(cfg, "processed", "channels")),
        "videos": str(_table_path(cfg, "processed", "videos")),
        "comments": str(_table_path(cfg, "processed", "comments")),
        "search_hits": str(_table_path(cfg, "processed", "search_hits")),
    }

    audit_file = root / "audit" / "runs" / f"run_{run_id}.json"
    audit_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
