from __future__ import annotations

import argparse
import json
import sys

from .collectors.transcripts import collect_transcript
from .config import load_config
from .pipeline import ALL_STAGES, run_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="youtube-pipeline", description="Coleta estruturada de dados públicos do YouTube.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-config", help="Valida o YAML sem chamar a API.")
    validate.add_argument("config")

    run = sub.add_parser("run", help="Executa o pipeline.")
    run.add_argument("config")
    run.add_argument(
        "--stages",
        default=",".join(ALL_STAGES),
        help=f"Stages separados por vírgula. Opções: {','.join(ALL_STAGES)}",
    )

    test_transcript = sub.add_parser(
        "test-transcript",
        help="Testa as estratégias de transcrição em um único video_id sem executar o pipeline inteiro.",
    )
    test_transcript.add_argument("config")
    test_transcript.add_argument("video_id")
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    try:
        if args.command == "validate-config":
            cfg = load_config(args.config)
            print(f"Configuração válida: projeto={cfg.name}")
            return

        if args.command == "run":
            stages = [x.strip() for x in args.stages.split(",") if x.strip()]
            report = run_pipeline(args.config, stages=stages)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return

        if args.command == "test-transcript":
            cfg = load_config(args.config)
            row, segments = collect_transcript(args.video_id, cfg)
            result = dict(row)
            result["segment_count"] = len(segments)
            # Evita despejar uma transcrição gigante no terminal.
            text = result.get("transcript_text")
            if text:
                result["transcript_preview"] = text[:1000]
                result["transcript_text"] = f"<{len(text)} caracteres; veja transcript_preview>"
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return
    except Exception as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
