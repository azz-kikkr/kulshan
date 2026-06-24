"""Resolve local CUR/Data Exports inputs."""

from __future__ import annotations

from pathlib import Path

from kulshan.cur.errors import CurDataError


def local_parquet_source(cur_path: str) -> str:
    """Return a DuckDB-readable Parquet path or glob for a local CUR input."""
    path = Path(cur_path)
    if not path.exists():
        raise CurDataError(f"Local CUR path does not exist: {cur_path}")
    if path.is_file():
        if path.suffix.lower() != ".parquet":
            raise CurDataError("Local CUR input must be a Parquet file or directory.")
        return path.as_posix()

    parquet_files = sorted(path.rglob("*.parquet"))
    if not parquet_files:
        raise CurDataError(f"No Parquet files found under local CUR path: {cur_path}")
    return (path / "**" / "*.parquet").as_posix()
