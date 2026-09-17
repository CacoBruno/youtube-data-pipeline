from __future__ import annotations

import time

import pandas as pd
from googleapiclient.errors import HttpError

from ..config import ProjectConfig
from ..utils import safe_int, utc_now_iso


def _author_channel_id(snippet: dict) -> str | None:
    value = snippet.get("authorChannelId")
    if isinstance(value, dict):
        return value.get("value")
    return None


def _comment_row(*, video_id: str, thread_id: str, comment: dict, parent_comment_id: str | None, is_reply: bool, total_reply_count: int | None = None) -> dict:
    snippet = comment.get("snippet") or {}
    return {
        "comment_id": comment.get("id"),
        "video_id": video_id,
        "thread_id": thread_id,
        "parent_comment_id": parent_comment_id,
        "is_reply": is_reply,
        "author_name": snippet.get("authorDisplayName"),
        "author_channel_id": _author_channel_id(snippet),
        "author_channel_url": snippet.get("authorChannelUrl"),
        "text": snippet.get("textDisplay") or snippet.get("textOriginal"),
        "like_count": safe_int(snippet.get("likeCount")),
        "published_at": snippet.get("publishedAt"),
        "updated_at": snippet.get("updatedAt"),
        "total_reply_count": total_reply_count if not is_reply else None,
        "captured_at": utc_now_iso(),
    }


def _fetch_all_replies(youtube, video_id: str, thread_id: str, parent_id: str, remaining: int, sleep_seconds: float) -> list[dict]:
    rows: list[dict] = []
    page_token = None
    while len(rows) < remaining:
        response = youtube.comments().list(
            part="snippet",
            parentId=parent_id,
            maxResults=min(100, remaining - len(rows)),
            pageToken=page_token,
            textFormat="plainText",
        ).execute()
        for comment in response.get("items", []):
            rows.append(
                _comment_row(
                    video_id=video_id,
                    thread_id=thread_id,
                    comment=comment,
                    parent_comment_id=parent_id,
                    is_reply=True,
                )
            )
            if len(rows) >= remaining:
                break
        page_token = response.get("nextPageToken")
        if not page_token:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return rows


def collect_comments_for_video(youtube, video_id: str, config: ProjectConfig) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    page_token = None
    status = {
        "video_id": video_id,
        "status": "completed",
        "rows_collected": 0,
        "error_type": None,
        "error_message": None,
        "captured_at": utc_now_iso(),
    }

    try:
        while len(rows) < config.comments.max_per_video:
            response = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=min(100, config.comments.max_per_video - len(rows)),
                pageToken=page_token,
                textFormat="plainText",
                order=config.comments.order,
            ).execute()

            items = response.get("items", [])
            if not items:
                break

            for item in items:
                thread_id = item.get("id")
                thread_snippet = item.get("snippet") or {}
                top = thread_snippet.get("topLevelComment") or {}
                top_id = top.get("id")
                total_replies = safe_int(thread_snippet.get("totalReplyCount")) or 0

                rows.append(
                    _comment_row(
                        video_id=video_id,
                        thread_id=thread_id,
                        comment=top,
                        parent_comment_id=None,
                        is_reply=False,
                        total_reply_count=total_replies,
                    )
                )
                if len(rows) >= config.comments.max_per_video:
                    break

                if config.comments.include_replies and total_replies > 0 and top_id:
                    remaining = config.comments.max_per_video - len(rows)
                    rows.extend(
                        _fetch_all_replies(
                            youtube,
                            video_id=video_id,
                            thread_id=thread_id,
                            parent_id=top_id,
                            remaining=remaining,
                            sleep_seconds=config.api.sleep_seconds,
                        )
                    )
                if len(rows) >= config.comments.max_per_video:
                    break

            page_token = response.get("nextPageToken")
            if not page_token or len(rows) >= config.comments.max_per_video:
                break
            if config.api.sleep_seconds:
                time.sleep(config.api.sleep_seconds)

    except HttpError as exc:
        text = str(exc)
        lower = text.lower()
        if "commentsdisabled" in lower:
            status["status"] = "comments_disabled"
        elif "videonotfound" in lower:
            status["status"] = "video_not_found"
        elif "quota" in lower:
            status["status"] = "quota_exceeded"
        else:
            status["status"] = "error"
        status["error_type"] = type(exc).__name__
        status["error_message"] = text

    status["rows_collected"] = len(rows)
    if status["status"] == "completed" and not rows:
        status["status"] = "no_comments"
    return pd.DataFrame(rows), status


def collect_comments(youtube, video_ids: list[str], config: ProjectConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_comments: list[pd.DataFrame] = []
    statuses: list[dict] = []
    for video_id in list(dict.fromkeys(x for x in video_ids if x)):
        df, status = collect_comments_for_video(youtube, video_id, config)
        if not df.empty:
            all_comments.append(df)
        statuses.append(status)
        if status.get("status") == "quota_exceeded" and config.api.stop_on_quota:
            break
        if config.api.sleep_seconds:
            time.sleep(config.api.sleep_seconds)

    comments = pd.concat(all_comments, ignore_index=True, sort=False) if all_comments else pd.DataFrame()
    return comments, pd.DataFrame(statuses)
