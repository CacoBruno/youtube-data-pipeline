from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd


def _serializable_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].map(
                lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)
                if isinstance(x, (dict, list, tuple))
                else x
            )
    return out


def read_table(base_path: Path) -> pd.DataFrame:
    parquet = base_path.with_suffix(".parquet")
    csv = base_path.with_suffix(".csv")
    if parquet.exists():
        return pd.read_parquet(parquet)
    if csv.exists():
        return pd.read_csv(csv)
    return pd.DataFrame()


def upsert(existing: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if existing.empty:
        result = new.copy()
    elif new.empty:
        result = existing.copy()
    else:
        result = pd.concat([existing, new], ignore_index=True, sort=False)
    if not result.empty and all(k in result.columns for k in keys):
        result = result.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)
    return result


def write_table(df: pd.DataFrame, base_path: Path, formats: Iterable[str]) -> None:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    safe = _serializable_df(df)
    formats = {x.lower() for x in formats}
    if "parquet" in formats:
        safe.to_parquet(base_path.with_suffix(".parquet"), index=False)
    if "csv" in formats:
        safe.to_csv(base_path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
