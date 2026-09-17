from __future__ import annotations

import time

import pandas as pd
from googleapiclient.errors import HttpError

from ..config import ProjectConfig
from ..utils import date_windows, stable_id, utc_now_iso


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "quota" in text or "dailylimit" in text


def discover_videos(youtube, config: ProjectConfig) -> pd.DataFrame:
    rows: list[dict] = []
    captured_at = utc_now_iso()
    windows = date_windows(config.search.start_date, config.search.end_date, config.search.window_days)

    for theme, raw_queries in config.search.themes.items():
        queries = list(dict.fromkeys(str(q).strip() for q in raw_queries if str(q).strip()))
        for query in queries:
            for order in config.search.orders:
                for published_after, published_before in windows:
                    search_id = stable_id(theme, query, order, published_after, published_before)
                    page_token = None
                    global_position = 0

                    for page_number in range(1, config.search.max_pages_per_query + 1):
                        params = {
                            "part": "snippet",
                            "q": query,
                            "type": "video",
                            "maxResults": 50,
                            "order": order,
                            "pageToken": page_token,
                            "publishedAfter": published_after,
                            "publishedBefore": published_before,
                            "regionCode": config.search.region_code,
                            "relevanceLanguage": config.search.relevance_language,
                        }
                        params = {k: v for k, v in params.items() if v is not None}
                        try:
                            response = youtube.search().list(**params).execute()
                        except HttpError as exc:
                            if _is_quota_error(exc) and config.api.stop_on_quota:
                                return pd.DataFrame(rows)
                            raise

                        for item in response.get("items", []):
                            video_id = (item.get("id") or {}).get("videoId")
                            snippet = item.get("snippet") or {}
                            if not video_id:
                                continue
                            global_position += 1
                            thumbnails = snippet.get("thumbnails") or {}
                            rows.append(
                                {
                                    "search_id": search_id,
                                    "theme": theme,
                                    "query": query,
                                    "search_order": order,
                                    "search_published_after": published_after,
                                    "search_published_before": published_before,
                                    "page_number": page_number,
                                    "result_position": global_position,
                                    "video_id": video_id,
                                    "channel_id": snippet.get("channelId"),
                                    "search_title": snippet.get("title"),
                                    "search_description": snippet.get("description"),
                                    "search_published_at": snippet.get("publishedAt"),
                                    "thumbnail_url": (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url"),
                                    "captured_at": captured_at,
                                }
                            )

                        page_token = response.get("nextPageToken")
                        if not page_token:
                            break
                        if config.api.sleep_seconds:
                            time.sleep(config.api.sleep_seconds)

    return pd.DataFrame(rows)
