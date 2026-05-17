"""Local file system scanner for backup source selection."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileInfo:
    """Metadata for a local file or directory."""
    path: str
    name: str
    size: int
    modified_at: float
    is_dir: bool = False
    relative_path: str = ""  # relative path from the scanned base dir, e.g. "sub/file.pdf"


class FileScanner:
    """Scans local paths and returns file manifests."""

    @staticmethod
    def scan_directory(dir_path: str, recursive: bool = True) -> list[FileInfo]:
        """Recursively scan a directory and return all files."""
        base = Path(dir_path)
        if not base.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {dir_path}")

        results: list[FileInfo] = []
        glob_pattern = "**/*" if recursive else "*"

        for entry in base.glob(glob_pattern):
            if not entry.is_file():
                continue
            stat = entry.stat()
            rel = entry.relative_to(base)
            results.append(FileInfo(
                path=str(entry),
                name=entry.name,
                size=stat.st_size,
                modified_at=stat.st_mtime,
                is_dir=False,
                relative_path=str(rel),
            ))
        return results

    @staticmethod
    def scan_paths(paths: list[str]) -> list[FileInfo]:
        """Accept mixed files/directories, expand directories recursively."""
        results: list[FileInfo] = []
        seen = set()

        for p in paths:
            path_obj = Path(p)
            if not path_obj.exists():
                raise FileNotFoundError(f"Path not found: {p}")

            if path_obj.is_dir():
                for fi in FileScanner.scan_directory(p, recursive=True):
                    if fi.path not in seen:
                        seen.add(fi.path)
                        results.append(fi)
            elif path_obj.is_file():
                if p not in seen:
                    seen.add(p)
                    stat = path_obj.stat()
                    results.append(FileInfo(
                        path=p,
                        name=path_obj.name,
                        size=stat.st_size,
                        modified_at=stat.st_mtime,
                        is_dir=False,
                    ))
        return results

    @staticmethod
    def filter_by_size(
        files: list[FileInfo],
        max_size: int | None = None,
        min_size: int | None = None,
    ) -> list[FileInfo]:
        """Filter files by size range (in bytes)."""
        results = files
        if min_size is not None:
            results = [f for f in results if f.size >= min_size]
        if max_size is not None:
            results = [f for f in results if f.size <= max_size]
        return results

    @staticmethod
    def filter_by_extension(
        files: list[FileInfo],
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> list[FileInfo]:
        """Filter files by extension (e.g. {'.txt', '.jpg'})."""
        if include is not None:
            return [f for f in files if Path(f.path).suffix.lower() in include]
        if exclude is not None:
            return [f for f in files if Path(f.path).suffix.lower() not in exclude]
        return files

    @staticmethod
    def find_duplicates(
        files: list[FileInfo],
        existing: list[dict],
    ) -> list[FileInfo]:
        """Find files that already exist in the destination (same name + size = duplicate)."""
        existing_set = {(e.get("name", ""), e.get("size", 0)) for e in existing}
        return [
            f for f in files
            if (f.name, f.size) not in existing_set
        ]
