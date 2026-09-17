from __future__ import annotations

import time

import pandas as pd

from ..config import ProjectConfig
from ..utils import chunks, parse_iso8601_duration_seconds, safe_int, utc_now_iso


def collect_videos(youtube, video_ids: list[str], config: ProjectConfig) -> pd.DataFrame:
    rows: list[dict] = []
    captured_at = utc_now_iso()
    ids = list(dict.fromkeys(x for x in video_ids if x))

    for batch in chunks(ids, 50):
        response = youtube.videos().list(
            part="snippet,contentDetails,statistics,status,topicDetails",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            content = item.get("contentDetails") or {}
            status = item.get("status") or {}
            topic = item.get("topicDetails") or {}
            thumbnails = snippet.get("thumbnails") or {}
            duration = content.get("duration")

            rows.append(
                {
                    "video_id": item.get("id"),
                    "channel_id": snippet.get("channelId"),
                    "channel_title": snippet.get("channelTitle"),
                    "title": snippet.get("title"),
                    "description": snippet.get("description"),
                    "published_at": snippet.get("publishedAt"),
                    "tags": snippet.get("tags") or [],
                    "category_id": snippet.get("categoryId"),
                    "default_language": snippet.get("defaultLanguage"),
                    "default_audio_language": snippet.get("defaultAudioLanguage"),
                    "live_broadcast_content": snippet.get("liveBroadcastContent"),
                    "thumbnail_url": (thumbnails.get("maxres") or thumbnails.get("standard") or thumbnails.get("high") or {}).get("url"),
                    "duration_iso8601": duration,
                    "duration_seconds": parse_iso8601_duration_seconds(duration),
                    "dimension": content.get("dimension"),
                    "definition": content.get("definition"),
                    "caption": content.get("caption"),
                    "licensed_content": content.get("licensedContent"),
                    "projection": content.get("projection"),
                    "view_count": safe_int(stats.get("viewCount")),
                    "like_count": safe_int(stats.get("likeCount")),
                    "comment_count": safe_int(stats.get("commentCount")),
                    "privacy_status": status.get("privacyStatus"),
                    "made_for_kids": status.get("madeForKids"),
                    "self_declared_made_for_kids": status.get("selfDeclaredMadeForKids"),
                    "topic_categories": topic.get("topicCategories") or [],
                    "video_url": f"https://www.youtube.com/watch?v={item.get('id')}",
                    "captured_at": captured_at,
                }
            )

        if config.api.sleep_seconds:
            time.sleep(config.api.sleep_seconds)

    return pd.DataFrame(rows)
