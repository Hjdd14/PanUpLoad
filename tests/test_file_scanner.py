"""Tests for FileScanner module."""

import pytest
import tempfile
import os
from pathlib import Path
from panupdate.core.file_scanner import FileScanner, FileInfo


class TestFileScanner:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            # Create test files
            Path(d, "a.txt").write_text("hello")
            Path(d, "b.jpg").write_text("image data")
            Path(d, "sub").mkdir()
            Path(d, "sub", "c.txt").write_text("nested")
            Path(d, "sub", "d.log").write_text("log data")
            yield Path(d)

    def test_scan_directory_flat(self, temp_dir):
        files = FileScanner.scan_directory(str(temp_dir), recursive=False)
        paths = {f.name for f in files}
        assert "a.txt" in paths
        assert "b.jpg" in paths
        assert "sub" not in paths  # sub is a dir, not a file

    def test_scan_directory_recursive(self, temp_dir):
        files = FileScanner.scan_directory(str(temp_dir), recursive=True)
        paths = {f.name for f in files}
        assert "a.txt" in paths
        assert "c.txt" in paths  # nested file
        assert "d.log" in paths
        assert len(files) == 4

    def test_scan_paths_mixed(self, temp_dir):
        file1 = str(temp_dir / "a.txt")
        dir_path = str(temp_dir / "sub")
        files = FileScanner.scan_paths([file1, dir_path])
        names = {f.name for f in files}
        assert "a.txt" in names
        assert "c.txt" in names
        assert "d.log" in names

    def test_scan_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            FileScanner.scan_directory("/nonexistent/path")

    def test_scan_paths_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            FileScanner.scan_paths(["/nonexistent/file.txt"])

    def test_filter_by_size(self, temp_dir):
        files = FileScanner.scan_directory(str(temp_dir), recursive=True)
        # a.txt is 5 bytes, b.jpg is 10 bytes, c.txt is 6 bytes, d.log is 8 bytes
        filtered = FileScanner.filter_by_size(files, min_size=7, max_size=9)
        assert len(filtered) == 1
        assert filtered[0].name == "d.log"

    def test_filter_by_extension_include(self, temp_dir):
        files = FileScanner.scan_directory(str(temp_dir), recursive=True)
        filtered = FileScanner.filter_by_extension(files, include={".txt"})
        assert all(f.name.endswith(".txt") for f in filtered)
        assert len(filtered) == 2  # a.txt and c.txt

    def test_filter_by_extension_exclude(self, temp_dir):
        files = FileScanner.scan_directory(str(temp_dir), recursive=True)
        filtered = FileScanner.filter_by_extension(files, exclude={".log"})
        assert all(not f.name.endswith(".log") for f in filtered)
        assert len(filtered) == 3

    def test_find_duplicates(self, temp_dir):
        files = FileScanner.scan_directory(str(temp_dir), recursive=True)
        existing = [
            {"name": "a.txt", "size": 5},   # duplicate
            {"name": "nonexistent.txt", "size": 100},  # not a duplicate
        ]
        deduped = FileScanner.find_duplicates(files, existing)
        names = {f.name for f in deduped}
        assert "a.txt" not in names  # duplicate removed
        assert "b.jpg" in names

    def test_empty_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        files = FileScanner.scan_directory(str(empty), recursive=True)
        assert files == []

    def test_file_info_dataclass(self):
        fi = FileInfo(path="/a/b.txt", name="b.txt", size=100, modified_at=123.0)
        assert fi.path == "/a/b.txt"
        assert fi.name == "b.txt"
        assert fi.size == 100
        assert fi.is_dir is False
