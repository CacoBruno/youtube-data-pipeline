from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

from ..config import ProjectConfig
from ..utils import utc_now_iso


def _clean_text(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text or None


def _language_candidates(languages: list[str]) -> list[str]:
    """Expande pt-BR -> pt sem duplicar, preservando prioridade."""
    out: list[str] = []
    for language in languages:
        language = str(language).strip()
        if not language:
            continue
        for candidate in (language, language.split("-")[0]):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


def _fetched_to_raw(fetched) -> list[dict]:
    if hasattr(fetched, "to_raw_data"):
        raw = fetched.to_raw_data()
        if raw:
            return raw
    raw: list[dict] = []
    for item in fetched:
        if isinstance(item, dict):
            raw.append(item)
        else:
            raw.append(
                {
                    "text": getattr(item, "text", None),
                    "start": getattr(item, "start", None),
                    "duration": getattr(item, "duration", None),
                }
            )
    return raw


def _transcript_row(video_id: str, transcript, fetched, method: str) -> tuple[dict | None, list[dict], dict | None]:
    raw = _fetched_to_raw(fetched)
    text = _clean_text(" ".join(str(x.get("text") or "") for x in raw))
    if not text:
        return None, [], {"type": "EmptyTranscript", "message": "Legenda encontrada sem texto útil."}

    row = {
        "video_id": video_id,
        "transcript_status": "success",
        "transcript_method": method,
        "transcript_language": getattr(transcript, "language_code", None),
        "transcript_text": text,
        "transcript_error": None,
        "transcript_captured_at": utc_now_iso(),
    }
    segments = [
        {
            "video_id": video_id,
            "segment_index": i,
            "start_seconds": item.get("start"),
            "duration_seconds": item.get("duration"),
            "text": item.get("text"),
            "source_method": method,
        }
        for i, item in enumerate(raw)
        if item.get("text")
    ]
    return row, segments, None


def _transcript_api(
    video_id: str,
    languages: list[str],
    use_any_available_caption: bool = True,
) -> tuple[dict | None, list[dict], dict | None]:
    """Tenta legenda manual, automática, preferida e, por fim, qualquer legenda disponível."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript_list = YouTubeTranscriptApi().list(video_id)
        candidates = _language_candidates(languages)
        attempts: list[tuple[str, object]] = []

        selectors = [
            ("youtube_transcript_api_manual", lambda: transcript_list.find_manually_created_transcript(candidates)),
            ("youtube_transcript_api_generated", lambda: transcript_list.find_generated_transcript(candidates)),
            ("youtube_transcript_api", lambda: transcript_list.find_transcript(candidates)),
        ]
        for method, selector in selectors:
            try:
                transcript = selector()
                attempts.append((method, transcript))
                break
            except Exception:
                continue

        if not attempts and use_any_available_caption:
            available = list(transcript_list)
            if available:
                # manual primeiro, depois gerada
                available.sort(key=lambda x: bool(getattr(x, "is_generated", False)))
                attempts.append(("youtube_transcript_api_any_language", available[0]))

        if not attempts:
            return None, [], {
                "type": "NoMatchingTranscript",
                "message": f"Nenhuma legenda disponível nos idiomas {candidates}.",
            }

        method, transcript = attempts[0]
        fetched = transcript.fetch()
        return _transcript_row(video_id, transcript, fetched, method)
    except Exception as exc:
        return None, [], {"type": type(exc).__name__, "message": str(exc)}


def _parse_subtitle_file(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".json3":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            parts = [seg.get("utf8", "") for event in data.get("events", []) for seg in event.get("segs", [])]
            return _clean_text(" ".join(parts))
        except Exception:
            return None

    try:
        content = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = Path(path).read_text(encoding="latin-1")
    content = re.sub(r"^WEBVTT.*?\n", "", content, flags=re.DOTALL)
    content = re.sub(r"(?m)^\d+\s*$", "", content)
    content = re.sub(r"(?m)^\d{2}:\d{2}(?::\d{2})?[\.,]\d{3}\s+-->.*$", "", content)
    content = re.sub(r"<[^>]+>", "", content)
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("NOTE")]
    return _clean_text(" ".join(lines))


def _cookies_args(config: ProjectConfig) -> list[str]:
    browser = config.transcripts.yt_dlp_cookies_from_browser
    return ["--cookies-from-browser", browser] if browser else []


def _ytdlp_subtitle(video_id: str, config: ProjectConfig) -> tuple[dict | None, dict | None]:
    tmp = tempfile.mkdtemp(prefix="youtube_pipeline_subs_")
    try:
        languages = _language_candidates(config.transcripts.languages)
        # pt.* alcança variações como pt-BR sem baixar todas as traduções automáticas.
        lang_patterns: list[str] = []
        for lg in languages:
            item = f"{lg}.*" if "-" not in lg else lg
            if item not in lang_patterns:
                lang_patterns.append(item)

        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--skip-download",
            "--write-sub",
            "--write-auto-sub",
            "--sub-langs",
            ",".join(lang_patterns),
            "--convert-subs",
            "vtt",
            "-o",
            os.path.join(tmp, "%(id)s.%(ext)s"),
            *_cookies_args(config),
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        files: list[str] = []
        for ext in ("*.vtt", "*.srt", "*.json3"):
            files.extend(glob.glob(os.path.join(tmp, ext)))
        if not files:
            message = (proc.stderr or proc.stdout or "Nenhuma legenda encontrada.").strip()
            return None, {"type": "YtDlpSubtitleError", "message": message[-4000:]}

        text = _parse_subtitle_file(files[0])
        if not text:
            return None, {"type": "EmptySubtitle", "message": "Legenda baixada sem texto útil."}

        filename = Path(files[0]).name
        detected_language = None
        for lg in languages:
            if lg.lower() in filename.lower():
                detected_language = lg
                break

        return {
            "video_id": video_id,
            "transcript_status": "success",
            "transcript_method": "yt_dlp_subtitle",
            "transcript_language": detected_language,
            "transcript_text": text,
            "transcript_error": None,
            "transcript_captured_at": utc_now_iso(),
        }, None
    except Exception as exc:
        return None, {"type": type(exc).__name__, "message": str(exc)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _download_audio(video_id: str, config: ProjectConfig, folder: str) -> tuple[str | None, dict | None]:
    """Baixa o áudio original sem conversão; faster-whisper/PyAV faz a decodificação."""
    outtmpl = os.path.join(folder, "%(id)s.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "-f",
        "bestaudio/best",
        "-o",
        outtmpl,
        *_cookies_args(config),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    candidates = [
        p for p in glob.glob(os.path.join(folder, f"{video_id}.*"))
        if Path(p).suffix.lower() not in {".part", ".ytdl", ".json"}
    ]
    if proc.returncode != 0 or not candidates:
        return None, {
            "type": "AudioDownloadError",
            "message": (proc.stderr or proc.stdout or "Falha ao baixar áudio.").strip()[-4000:],
        }
    candidates.sort(key=lambda p: Path(p).stat().st_size, reverse=True)
    return candidates[0], None


def _whisper(video_id: str, config: ProjectConfig) -> tuple[dict | None, list[dict], dict | None]:
    tmp = tempfile.mkdtemp(prefix="youtube_pipeline_audio_")
    try:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            return None, [], {
                "type": type(exc).__name__,
                "message": "faster-whisper não está instalado ou não pôde ser importado. Use Python 3.11–3.13 e reinstale o projeto.",
            }

        audio_path, download_error = _download_audio(video_id, config, tmp)
        if not audio_path:
            return None, [], download_error

        model = WhisperModel(
            config.transcripts.whisper_model_size,
            device=config.transcripts.whisper_device,
            compute_type=config.transcripts.whisper_compute_type,
        )
        kwargs = {"vad_filter": config.transcripts.whisper_vad_filter}
        if config.transcripts.whisper_language:
            kwargs["language"] = config.transcripts.whisper_language
        segments_iter, info = model.transcribe(audio_path, **kwargs)

        segments: list[dict] = []
        parts: list[str] = []
        for i, seg in enumerate(segments_iter):
            txt = _clean_text(getattr(seg, "text", None))
            if not txt:
                continue
            parts.append(txt)
            start = getattr(seg, "start", None)
            end = getattr(seg, "end", None)
            duration = (end - start) if start is not None and end is not None else None
            segments.append(
                {
                    "video_id": video_id,
                    "segment_index": i,
                    "start_seconds": start,
                    "duration_seconds": duration,
                    "text": txt,
                    "source_method": f"faster_whisper_{config.transcripts.whisper_model_size}",
                }
            )

        text = _clean_text(" ".join(parts))
        if not text:
            return None, [], {"type": "EmptyWhisperTranscript", "message": "Whisper não produziu texto."}

        return {
            "video_id": video_id,
            "transcript_status": "success",
            "transcript_method": f"faster_whisper_{config.transcripts.whisper_model_size}",
            "transcript_language": getattr(info, "language", None),
            "transcript_text": text,
            "transcript_error": None,
            "transcript_captured_at": utc_now_iso(),
        }, segments, None
    except Exception as exc:
        return None, [], {"type": type(exc).__name__, "message": str(exc)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def collect_transcript(video_id: str, config: ProjectConfig) -> tuple[dict, list[dict]]:
    errors: dict[str, dict] = {}

    row, segments, error = _transcript_api(
        video_id,
        config.transcripts.languages,
        config.transcripts.use_any_available_caption,
    )
    if row:
        return row, segments
    if error:
        errors["youtube_transcript_api"] = error

    if config.transcripts.use_ytdlp:
        row, error = _ytdlp_subtitle(video_id, config)
        if row:
            return row, []
        if error:
            errors["yt_dlp_subtitle"] = error

    if config.transcripts.use_whisper:
        row, segments, error = _whisper(video_id, config)
        if row:
            return row, segments
        if error:
            errors["whisper"] = error

    return {
        "video_id": video_id,
        "transcript_status": "failed",
        "transcript_method": "failed",
        "transcript_language": None,
        "transcript_text": None,
        "transcript_error": errors,
        "transcript_captured_at": utc_now_iso(),
    }, []


def collect_transcripts(video_ids: list[str], config: ProjectConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    segments: list[dict] = []
    unique_ids = list(dict.fromkeys(x for x in video_ids if x))
    for i, video_id in enumerate(unique_ids, start=1):
        print(f"    transcript {i}/{len(unique_ids)} | video_id={video_id}")
        row, segs = collect_transcript(video_id, config)
        print(f"      -> {row.get('transcript_status')} | {row.get('transcript_method')}")
        rows.append(row)
        segments.extend(segs)
    return pd.DataFrame(rows), pd.DataFrame(segments)
