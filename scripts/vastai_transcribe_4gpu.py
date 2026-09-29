from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import pandas as pd

from youtube_pipeline.config import load_config
from youtube_pipeline.collectors.transcripts import collect_transcript
from youtube_pipeline.pipeline import _merge_transcripts_into_videos
from youtube_pipeline.storage import write_table
from youtube_pipeline.utils import utc_now_iso


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcreve videos em paralelo usando uma GPU por processo."
    )
    parser.add_argument("--config", required=True, help="YAML do projeto.")
    parser.add_argument("--videos", required=True, help="videos.parquet ou videos.csv.")
    parser.add_argument("--output-dir", required=True, help="Diretorio de saida/checkpoints.")
    parser.add_argument("--gpus", default="0,1,2,3", help="IDs CUDA separados por virgula.")
    parser.add_argument("--model", default="small", help="Modelo faster-whisper.")
    parser.add_argument("--compute-type", default="float16", help="Ex.: float16 ou int8_float16.")
    parser.add_argument("--limit", type=int, default=None, help="Limita videos pendentes para smoke test.")
    return parser.parse_args()


def _read_videos(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Base de videos nao encontrada: {path}")
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError("--videos deve apontar para .parquet ou .csv")
    if "video_id" not in df.columns:
        raise ValueError("A base de videos precisa conter a coluna video_id.")
    return df


def _checkpoint_files(output_dir: Path) -> list[Path]:
    folder = output_dir / "checkpoints"
    folder.mkdir(parents=True, exist_ok=True)
    return sorted(folder.glob("gpu_*.jsonl"))


def _load_latest_records(output_dir: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for path in _checkpoint_files(output_dir):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    # Uma ultima linha truncada pode existir se a instancia caiu durante a escrita.
                    continue
                video_id = str(rec.get("video_id") or "").strip()
                if video_id:
                    previous = latest.get(video_id)
                    previous_ts = str((previous or {}).get("completed_at") or "")
                    current_ts = str(rec.get("completed_at") or "")
                    if previous is None or current_ts >= previous_ts:
                        latest[video_id] = rec
    return latest


def _worker(
    gpu_id: int,
    config_path: str,
    output_dir: str,
    model_size: str,
    compute_type: str,
    task_queue,
) -> None:
    # Cada processo enxerga apenas uma GPU. Dentro dele, CUDA device 0 e a GPU fisica gpu_id.
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    from faster_whisper import WhisperModel

    cfg = load_config(config_path)
    cfg.transcripts.enabled = True
    cfg.transcripts.use_whisper = True
    cfg.transcripts.retry_failed = True
    cfg.transcripts.whisper_model_size = model_size
    cfg.transcripts.whisper_device = "cuda"
    cfg.transcripts.whisper_compute_type = compute_type

    print(
        f"[GPU {gpu_id}] carregando modelo={model_size} compute_type={compute_type}",
        flush=True,
    )
    model = WhisperModel(
        model_size,
        device="cuda",
        device_index=0,
        compute_type=compute_type,
    )
    print(f"[GPU {gpu_id}] modelo pronto", flush=True)

    checkpoint = Path(output_dir) / "checkpoints" / f"gpu_{gpu_id}.jsonl"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)

    processed = 0
    while True:
        video_id = task_queue.get()
        if video_id is None:
            break

        processed += 1
        started_at = utc_now_iso()
        try:
            row, segments = collect_transcript(
                video_id,
                cfg,
                whisper_model=model,
            )
        except Exception as exc:
            row = {
                "video_id": video_id,
                "transcript_status": "failed",
                "transcript_method": "worker_exception",
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

        # Um registro completo por linha. Flush a cada video = checkpoint simples e retomavel.
        with checkpoint.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            fh.flush()

        print(
            f"[GPU {gpu_id}] #{processed} | {video_id} | "
            f"{row.get('transcript_status')} | {row.get('transcript_method')}",
            flush=True,
        )


def _consolidate(videos: pd.DataFrame, output_dir: Path) -> dict:
    latest = _load_latest_records(output_dir)
    rows: list[dict] = []
    segments: list[dict] = []

    for rec in latest.values():
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
            ["video_id", "segment_index", "source_method"], keep="last"
        ).reset_index(drop=True)

    write_table(transcripts, output_dir / "transcripts", ["parquet", "csv"])
    write_table(segments_df, output_dir / "transcript_segments", ["parquet", "csv"])

    merged = _merge_transcripts_into_videos(videos, transcripts)
    write_table(merged, output_dir / "videos_with_transcripts", ["parquet", "csv"])

    summary = {
        "generated_at": utc_now_iso(),
        "videos_input": int(videos["video_id"].nunique()),
        "transcript_rows": int(len(transcripts)),
        "successes": (
            int(transcripts["transcript_status"].eq("success").sum())
            if not transcripts.empty and "transcript_status" in transcripts.columns
            else 0
        ),
        "failures": (
            int(transcripts["transcript_status"].eq("failed").sum())
            if not transcripts.empty and "transcript_status" in transcripts.columns
            else 0
        ),
        "methods": (
            {
                str(method): int(count)
                for method, count in transcripts["transcript_method"].value_counts(dropna=False).items()
            }
            if not transcripts.empty and "transcript_method" in transcripts.columns
            else {}
        ),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = _parse_args()

    gpu_ids = [int(x.strip()) for x in args.gpus.split(",") if x.strip()]
    if not gpu_ids:
        raise ValueError("Informe pelo menos uma GPU em --gpus.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = _read_videos(args.videos)
    video_ids = videos["video_id"].dropna().astype(str).drop_duplicates().tolist()

    previous = _load_latest_records(output_dir)
    successful = {
        video_id
        for video_id, rec in previous.items()
        if (rec.get("row") or {}).get("transcript_status") == "success"
    }

    pending = [video_id for video_id in video_ids if video_id not in successful]
    if args.limit is not None:
        pending = pending[: args.limit]

    print("=" * 72)
    print("VAST.AI MULTI-GPU TRANSCRIPTS")
    print(f"videos na base       : {len(video_ids)}")
    print(f"sucessos em checkpoint: {len(successful)}")
    print(f"pendentes nesta rodada: {len(pending)}")
    print(f"GPUs                  : {gpu_ids}")
    print(f"modelo                : {args.model}")
    print(f"compute_type          : {args.compute_type}")
    print("=" * 72)

    if pending:
        ctx = mp.get_context("spawn")
        # Fila sem limite pratico: se um worker falhar na inicializacao, o processo
        # principal ainda consegue terminar o enqueue e diagnosticar os exit codes.
        task_queue = ctx.Queue()

        workers = [
            ctx.Process(
                target=_worker,
                args=(
                    gpu_id,
                    args.config,
                    str(output_dir),
                    args.model,
                    args.compute_type,
                    task_queue,
                ),
                name=f"whisper-gpu-{gpu_id}",
            )
            for gpu_id in gpu_ids
        ]

        for worker in workers:
            worker.start()

        for video_id in pending:
            task_queue.put(video_id)

        for _ in workers:
            task_queue.put(None)

        for worker in workers:
            worker.join()

        failed_workers = [
            (worker.name, worker.exitcode)
            for worker in workers
            if worker.exitcode not in (0, None)
        ]
        if failed_workers:
            print(f"ERRO: workers com falha: {failed_workers}", file=sys.stderr)

    else:
        failed_workers = []

    summary = _consolidate(videos, output_dir)
    print("\nRESUMO CONSOLIDADO")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if failed_workers:
        raise RuntimeError(f"Worker(s) GPU falharam: {failed_workers}")


if __name__ == "__main__":
    main()
