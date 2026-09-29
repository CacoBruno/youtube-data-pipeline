from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path

import pandas as pd

from youtube_pipeline.pipeline import _merge_transcripts_into_videos
from youtube_pipeline.storage import write_table
from youtube_pipeline.utils import utc_now_iso


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcreve na Vast.ai audios preparados localmente, sem acessar o YouTube."
    )
    parser.add_argument("--prepared-dir", required=True)
    parser.add_argument("--videos", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--gpus", default="0,1,2,3")
    parser.add_argument("--model", default="small")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def _read_videos(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError("--videos deve apontar para .parquet ou .csv")
    if "video_id" not in df.columns:
        raise ValueError("A base de videos precisa conter video_id.")
    return df


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _latest_records(paths: list[Path]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for path in paths:
        for rec in _read_jsonl(path):
            video_id = str(rec.get("video_id") or "").strip()
            if not video_id:
                continue
            previous = latest.get(video_id)
            previous_ts = str(
                (previous or {}).get("completed_at")
                or (previous or {}).get("captured_at")
                or ""
            )
            current_ts = str(rec.get("completed_at") or rec.get("captured_at") or "")
            if previous is None or current_ts >= previous_ts:
                latest[video_id] = rec
    return latest


def _worker(
    gpu_id: int,
    task_queue,
    checkpoint_path: str,
    model_name: str,
    compute_type: str,
    language: str | None,
) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    from faster_whisper import WhisperModel

    print(f"[GPU {gpu_id}] carregando modelo={model_name}", flush=True)
    model = WhisperModel(
        model_name,
        device="cuda",
        device_index=0,
        compute_type=compute_type,
    )
    print(f"[GPU {gpu_id}] modelo pronto", flush=True)

    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    processed = 0

    while True:
        item = task_queue.get()
        if item is None:
            break

        video_id, audio_path = item
        processed += 1
        started_at = utc_now_iso()

        try:
            kwargs = {"vad_filter": True}
            if language:
                kwargs["language"] = language

            segments_iter, info = model.transcribe(audio_path, **kwargs)
            parts: list[str] = []
            segments: list[dict] = []

            for i, seg in enumerate(segments_iter):
                text = str(getattr(seg, "text", "") or "").strip()
                if not text:
                    continue
                parts.append(text)
                start = getattr(seg, "start", None)
                end = getattr(seg, "end", None)
                segments.append(
                    {
                        "video_id": video_id,
                        "segment_index": i,
                        "start_seconds": start,
                        "duration_seconds": (
                            end - start
                            if start is not None and end is not None
                            else None
                        ),
                        "text": text,
                        "source_method": f"faster_whisper_{model_name}",
                    }
                )

            transcript_text = " ".join(parts).strip()
            if not transcript_text:
                raise ValueError("Whisper nao produziu texto.")

            row = {
                "video_id": video_id,
                "transcript_status": "success",
                "transcript_method": f"faster_whisper_{model_name}",
                "transcript_language": getattr(info, "language", None),
                "transcript_text": transcript_text,
                "transcript_error": None,
                "transcript_captured_at": utc_now_iso(),
            }
        except Exception as exc:
            row = {
                "video_id": video_id,
                "transcript_status": "failed",
                "transcript_method": "failed",
                "transcript_language": None,
                "transcript_text": None,
                "transcript_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "transcript_captured_at": utc_now_iso(),
            }
            segments = []

        record = {
            "video_id": video_id,
            "gpu_id": gpu_id,
            "started_at": started_at,
            "completed_at": utc_now_iso(),
            "row": row,
            "segments": segments,
        }

        with checkpoint.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            fh.flush()

        print(
            f"[GPU {gpu_id}] #{processed} | {video_id} | "
            f"{row.get('transcript_status')}",
            flush=True,
        )


def main() -> None:
    args = _parse_args()

    prepared_dir = Path(args.prepared_dir)
    prepare_checkpoint = prepared_dir / "prepare_checkpoint.jsonl"
    audio_dir = prepared_dir / "audio"
    if not prepare_checkpoint.exists():
        raise FileNotFoundError(
            f"Checkpoint de preparacao nao encontrado: {prepare_checkpoint}"
        )

    videos = _read_videos(args.videos)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_checkpoint_dir = output_dir / "checkpoints"
    worker_checkpoint_dir.mkdir(parents=True, exist_ok=True)

    gpu_ids = [int(x.strip()) for x in args.gpus.split(",") if x.strip()]
    if not gpu_ids:
        raise ValueError("Informe ao menos uma GPU em --gpus.")

    prepared = _latest_records([prepare_checkpoint])
    prior_gpu = _latest_records(sorted(worker_checkpoint_dir.glob("gpu_*.jsonl")))

    caption_rows: list[dict] = []
    audio_items: list[tuple[str, str]] = []
    missing_audio: list[str] = []

    for video_id, rec in prepared.items():
        if rec.get("status") == "caption" and isinstance(rec.get("row"), dict):
            caption_rows.append(dict(rec["row"]))
            continue
        if rec.get("status") != "audio":
            continue

        previous_row = (prior_gpu.get(video_id) or {}).get("row") or {}
        if previous_row.get("transcript_status") == "success":
            continue

        audio_file = rec.get("audio_file")
        path = audio_dir / str(audio_file) if audio_file else None
        if path and path.exists():
            audio_items.append((video_id, str(path)))
        else:
            missing_audio.append(video_id)

    if args.limit is not None:
        audio_items = audio_items[: args.limit]

    print("=" * 72)
    print("VAST OFFLINE TRANSCRIPTION")
    print(f"captions prontas       : {len(caption_rows)}")
    print(f"audios para whisper    : {len(audio_items)}")
    print(f"audios ausentes        : {len(missing_audio)}")
    print(f"GPUs                    : {gpu_ids}")
    print(f"modelo                  : {args.model}")
    print(f"compute_type            : {args.compute_type}")
    print("=" * 72)

    if audio_items:
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        workers = []
        for gpu_id in gpu_ids:
            checkpoint = worker_checkpoint_dir / f"gpu_{gpu_id}.jsonl"
            worker = ctx.Process(
                target=_worker,
                args=(
                    gpu_id,
                    queue,
                    str(checkpoint),
                    args.model,
                    args.compute_type,
                    args.language,
                ),
                name=f"whisper-gpu-{gpu_id}",
            )
            worker.start()
            workers.append(worker)

        for item in audio_items:
            queue.put(item)
        for _ in workers:
            queue.put(None)
        for worker in workers:
            worker.join()

        failed_workers = [
            (worker.name, worker.exitcode)
            for worker in workers
            if worker.exitcode not in (0, None)
        ]
        if failed_workers:
            raise RuntimeError(f"Worker(s) falharam: {failed_workers}")

    gpu_latest = _latest_records(sorted(worker_checkpoint_dir.glob("gpu_*.jsonl")))

    rows = list(caption_rows)
    segments: list[dict] = []
    for rec in gpu_latest.values():
        row = rec.get("row")
        if isinstance(row, dict):
            row = dict(row)
            row["gpu_id"] = rec.get("gpu_id")
            rows.append(row)
        for seg in rec.get("segments") or []:
            if isinstance(seg, dict):
                seg = dict(seg)
                seg["gpu_id"] = rec.get("gpu_id")
                segments.append(seg)

    transcripts = pd.DataFrame(rows)
    if not transcripts.empty:
        transcripts = transcripts.drop_duplicates("video_id", keep="last").reset_index(drop=True)

    segments_df = pd.DataFrame(segments)
    if not segments_df.empty:
        segments_df = segments_df.drop_duplicates(
            ["video_id", "segment_index", "source_method"],
            keep="last",
        ).reset_index(drop=True)

    write_table(transcripts, output_dir / "transcripts", ["parquet", "csv"])
    write_table(
        segments_df,
        output_dir / "transcript_segments",
        ["parquet", "csv"],
    )

    merged = _merge_transcripts_into_videos(videos, transcripts)
    write_table(
        merged,
        output_dir / "videos_with_transcripts",
        ["parquet", "csv"],
    )

    summary = {
        "generated_at": utc_now_iso(),
        "videos_input": int(videos["video_id"].nunique()),
        "prepared_records": len(prepared),
        "caption_rows": len(caption_rows),
        "audio_candidates": len(
            [rec for rec in prepared.values() if rec.get("status") == "audio"]
        ),
        "missing_audio": len(missing_audio),
        "transcript_rows": len(transcripts),
        "successes": (
            int(transcripts["transcript_status"].eq("success").sum())
            if not transcripts.empty
            else 0
        ),
        "failures": (
            int(transcripts["transcript_status"].eq("failed").sum())
            if not transcripts.empty
            else 0
        ),
        "methods": (
            {
                str(method): int(count)
                for method, count in transcripts["transcript_method"]
                .value_counts(dropna=False)
                .items()
            }
            if not transcripts.empty
            else {}
        ),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nRESUMO CONSOLIDADO")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
