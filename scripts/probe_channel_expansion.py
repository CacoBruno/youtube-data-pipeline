from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from googleapiclient.discovery import build


PROJECT = "smoke_youtube_v02"
MAX_CHANNELS = 10
MAX_PAGES_PER_CHANNEL = 2
MAX_RESULTS_PER_PAGE = 50

START_DATE = "2026-09-01"
END_DATE = "2026-09-16"


def main() -> None:
    load_dotenv()

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY não encontrada no ambiente/.env")

    root = Path("data") / PROJECT

    hits = pd.read_csv(root / "processed" / "search_hits.csv")
    channels = pd.read_csv(root / "processed" / "channels.csv")

    youtube = build(
        "youtube",
        "v3",
        developerKey=api_key,
    )

    # Canais na ordem em que apareceram no discovery.
    discovered_channel_ids = (
        hits["channel_id"]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .head(MAX_CHANNELS)
        .tolist()
    )

    selected_channels = channels[
        channels["channel_id"].astype(str).isin(discovered_channel_ids)
    ].copy()

    search_video_ids = set(
        hits["video_id"].dropna().astype(str)
    )

    rows = []

    for _, channel in selected_channels.iterrows():
        channel_id = str(channel["channel_id"])
        channel_title = channel.get("channel_title")
        playlist_id = channel.get("uploads_playlist_id")

        if pd.isna(playlist_id):
            continue

        print(f"channel={channel_title} | {channel_id}")

        page_token = None

        for page_number in range(1, MAX_PAGES_PER_CHANNEL + 1):

            response = (
                youtube.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=str(playlist_id),
                    maxResults=MAX_RESULTS_PER_PAGE,
                    pageToken=page_token,
                )
                .execute()
            )

            for position, item in enumerate(
                response.get("items", []),
                start=1,
            ):
                snippet = item.get("snippet") or {}
                content = item.get("contentDetails") or {}

                video_id = (
                    content.get("videoId")
                    or (snippet.get("resourceId") or {}).get("videoId")
                )

                published_at = (
                    content.get("videoPublishedAt")
                    or snippet.get("publishedAt")
                )

                if not video_id or not published_at:
                    continue

                published_date = published_at[:10]

                if not (START_DATE <= published_date <= END_DATE):
                    continue

                rows.append(
                    {
                        "channel_id": channel_id,
                        "channel_title": channel_title,
                        "uploads_playlist_id": playlist_id,
                        "page_number": page_number,
                        "position_in_page": position,
                        "video_id": video_id,
                        "title": snippet.get("title"),
                        "description": snippet.get("description"),
                        "published_at": published_at,
                        "already_in_search": video_id in search_video_ids,
                    }
                )

            page_token = response.get("nextPageToken")

            if not page_token:
                break

    expansion = pd.DataFrame(rows)

    if not expansion.empty:
        expansion = expansion.drop_duplicates(
            ["channel_id", "video_id"]
        ).reset_index(drop=True)

        expansion["new_vs_search"] = ~expansion["already_in_search"]

        # Revisão manual, se quisermos avaliar relevância.
        expansion["relevance_label"] = ""
        expansion["relevance_reason"] = ""

    output_dir = root / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "channel_expansion_review.csv"

    expansion.to_csv(
        output_file,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(f"Arquivo: {output_file}")
    print(f"Candidatos na janela: {len(expansion)}")

    if not expansion.empty:
        print(
            "Já encontrados pelas queries:",
            int(expansion["already_in_search"].sum()),
        )

        print(
            "Novos em relação às queries:",
            int(expansion["new_vs_search"].sum()),
        )


if __name__ == "__main__":
    main()