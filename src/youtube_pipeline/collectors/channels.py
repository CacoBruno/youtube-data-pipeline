from __future__ import annotations

import time

import pandas as pd

from ..config import ProjectConfig
from ..utils import chunks, safe_int, utc_now_iso


def collect_channels(youtube, channel_ids: list[str], config: ProjectConfig) -> pd.DataFrame:
    rows: list[dict] = []
    captured_at = utc_now_iso()
    ids = list(dict.fromkeys(x for x in channel_ids if x))

    for batch in chunks(ids, 50):
        response = youtube.channels().list(
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
            playlists = content.get("relatedPlaylists") or {}

            rows.append(
                {
                    "channel_id": item.get("id"),
                    "channel_title": snippet.get("title"),
                    "channel_description": snippet.get("description"),
                    "channel_published_at": snippet.get("publishedAt"),
                    "country": snippet.get("country"),
                    "custom_url": snippet.get("customUrl"),
                    "thumbnail_url": (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url"),
                    "subscriber_count": safe_int(stats.get("subscriberCount")),
                    "hidden_subscriber_count": stats.get("hiddenSubscriberCount"),
                    "channel_view_count": safe_int(stats.get("viewCount")),
                    "video_count": safe_int(stats.get("videoCount")),
                    "uploads_playlist_id": playlists.get("uploads"),
                    "privacy_status": status.get("privacyStatus"),
                    "made_for_kids": status.get("madeForKids"),
                    "topic_categories": topic.get("topicCategories") or [],
                    "channel_url": f"https://www.youtube.com/channel/{item.get('id')}",
                    "captured_at": captured_at,
                }
            )

        if config.api.sleep_seconds:
            time.sleep(config.api.sleep_seconds)

    return pd.DataFrame(rows)
