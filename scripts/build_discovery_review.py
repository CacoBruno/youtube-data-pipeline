from pathlib import Path

import pandas as pd


PROJECT = "smoke_youtube_v02"
N_PER_QUERY = 20

root = Path("data") / PROJECT

hits = pd.read_csv(root / "processed" / "search_hits.csv")
videos = pd.read_csv(root / "processed" / "videos.csv")

# Como houve mais de uma execução de discovery, o mesmo vídeo pode
# reaparecer dentro da mesma query em posições diferentes.
# Para a avaliação, mantemos a melhor posição observada.
sample = (
    hits.sort_values(["query", "result_position"])
    .drop_duplicates(["query", "video_id"], keep="first")
    .groupby("query", group_keys=False)
    .head(N_PER_QUERY)
    .copy()
)

# Trazemos os metadados mais completos da tabela de vídeos.
video_cols = [
    "video_id",
    "channel_title",
    "title",
    "description",
    "published_at",
    "duration_seconds",
    "view_count",
    "video_url",
]

review = sample[
    [
        "query",
        "result_position",
        "video_id",
    ]
].merge(
    videos[video_cols],
    on="video_id",
    how="left",
)

# Campos que serão preenchidos manualmente.
review["relevance_label"] = ""
review["relevance_reason"] = ""

# Ordenação para facilitar a revisão.
review = review.sort_values(
    ["query", "result_position", "video_id"]
).reset_index(drop=True)

output_dir = root / "evaluation"
output_dir.mkdir(parents=True, exist_ok=True)

output_file = output_dir / "discovery_review.csv"

review.to_csv(
    output_file,
    index=False,
    encoding="utf-8-sig",
)

print(f"Arquivo criado: {output_file}")
print(f"Linhas: {len(review)}")

print("\nVídeos por query:")
print(review.groupby("query")["video_id"].count())