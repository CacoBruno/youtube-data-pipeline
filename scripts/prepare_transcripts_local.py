from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from youtube_pipeline.collectors.transcripts import _download_audio, collect_transcript
from youtube_pipeline.config import load_config
from youtube_pipeline.utils import utc_now_iso


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepara legendas e audios localmente para transcricao GPU offline na Vast.ai."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--videos", required=True)
    parser.add_argument("--output-dir", required=True)
    # Mantido por compatibilidade com comandos anteriores. A preparação é
    # deliberadamente serial para reduzir risco de rate limit/IP block.
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=5.0,
        help="Pausa entre vídeos para reduzir pressão sobre o YouTube.",
    )
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


def _load_latest(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            video_id = str(rec.get("video_id") or "").strip()
            if video_id:
                latest[video_id] = rec
    return latest


def _contains_rate_limit(value) -> bool:
    """Detecta sinais observáveis de bloqueio/rate limit sem mascarar outros erros."""
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_contains_rate_limit(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_rate_limit(v) for v in value)
    text = str(value).lower()
    signals = (
        "ipblocked",
        "too many requests",
        "http error 429",
        "sign in to confirm you’re not a bot",
        "sign in to confirm you're not a bot",
    )
    return any(signal in text for signal in signals)


def _prepare_one(video_id: str, config_path: str, audio_dir: str) -> dict:
    cfg = load_config(config_path)
    cfg.transcripts.enabled = True
    cfg.transcripts.use_whisper = False

    row, _ = collect_transcript(video_id, cfg)
    if row.get("transcript_status") == "success":
        return {
            "video_id": video_id,
            "status": "caption",
            "captured_at": utc_now_iso(),
            "row": row,
            "audio_file": None,
            "error": None,
        }

    audio_path, audio_error = _download_audio(video_id, cfg, audio_dir)
    if audio_path:
        return {
            "video_id": video_id,
            "status": "audio",
            "captured_at": utc_now_iso(),
            "row": None,
            "audio_file": Path(audio_path).name,
            "error": row.get("transcript_error"),
        }

    return {
        "video_id": video_id,
        "status": "failed",
        "captured_at": utc_now_iso(),
        "row": None,
        "audio_file": None,
        "error": {
            "caption": row.get("transcript_error"),
            "audio_download": audio_error,
        },
    }


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / "prepare_checkpoint.jsonl"

    videos = _read_videos(args.videos)
    video_ids = videos["video_id"].dropna().astype(str).drop_duplicates().tolist()

    previous = _load_latest(checkpoint)
    successful: set[str] = set()
    for video_id, rec in previous.items():
        if rec.get("status") == "caption":
            successful.add(video_id)
        elif rec.get("status") == "audio":
            audio_file = rec.get("audio_file")
            if audio_file and (audio_dir / str(audio_file)).exists():
                successful.add(video_id)

    pending = [video_id for video_id in video_ids if video_id not in successful]
    if args.limit is not None:
        pending = pending[: args.limit]

    print("=" * 72)
    print("PREPARE TRANSCRIPTS - LOCAL")
    print(f"videos na base : {len(video_ids)}")
    print(f"ja preparados  : {len(successful)}")
    print(f"pendentes      : {len(pending)}")
    print(f"workers rede   : {args.workers}")
    print("=" * 72)

    if args.workers != 1:
        print(
            "AVISO: --workers foi mantido por compatibilidade, mas esta versão "
            "processa de forma serial para reduzir bloqueios.",
            flush=True,
        )

    stopped_by_rate_limit = False
    with checkpoint.open("a", encoding="utf-8") as fh:
        for position, video_id in enumerate(pending, start=1):
            try:
                rec = _prepare_one(video_id, args.config, str(audio_dir))
            except Exception as exc:
                rec = {
                    "video_id": video_id,
                    "status": "failed",
                    "captured_at": utc_now_iso(),
                    "row": None,
                    "audio_file": None,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }

            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            print(f"{video_id} | {rec.get('status')}", flush=True)

            if rec.get("status") == "failed" and _contains_rate_limit(rec.get("error")):
                stopped_by_rate_limit = True
                print(
                    "RATE LIMIT/IP BLOCK detectado. Execução interrompida "
                    "sem consumir os demais vídeos pendentes.",
                    flush=True,
                )
                break

            if position < len(pending) and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    latest = _load_latest(checkpoint)
    counts: dict[str, int] = {}
    for rec in latest.values():
        status = str(rec.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    summary = {
        "generated_at": utc_now_iso(),
        "videos_input": len(video_ids),
        "prepared_records": len(latest),
        "status_counts": counts,
        "audio_dir": str(audio_dir),
        "stopped_by_rate_limit": stopped_by_rate_limit,
        "sleep_seconds": args.sleep_seconds,
    }
    (output_dir / "prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nRESUMO")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
