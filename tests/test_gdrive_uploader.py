"""gdrive_uploader 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xp.config import GDriveConfig
from xp.gdrive_uploader import GDriveUploader, _ensure_subfolder


@pytest.fixture
def gdrive_config():
    return GDriveConfig(
        sa_key_path="fake_sa.json",
        folder_id="root_folder_123",
    )


class TestEnsureSubfolder:
    def test_existing_folder_returns_id(self):
        """이미 존재하는 폴더의 ID를 반환합니다."""
        mock_service = MagicMock()
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "existing_id_456"}]
        }

        result = _ensure_subfolder(mock_service, "parent_id", "test-folder")
        assert result == "existing_id_456"

    def test_new_folder_creates_and_returns_id(self):
        """폴더가 없으면 새로 만들고 ID를 반환합니다."""
        mock_service = MagicMock()
        # list 결과: 빈 리스트
        mock_service.files().list().execute.return_value = {"files": []}
        # create 결과
        mock_service.files().create().execute.return_value = {"id": "new_id_789"}

        result = _ensure_subfolder(mock_service, "parent_id", "new-folder")
        assert result == "new_id_789"


class TestGDriveUploader:
    @patch("xp.gdrive_uploader._get_drive_service")
    def test_upload_file(self, mock_get_service, gdrive_config, tmp_path):
        """단일 파일 업로드를 테스트합니다."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # create 응답 mock
        mock_service.files().create().execute.return_value = {
            "id": "file_id_001",
            "name": "test.txt",
            "webViewLink": "https://drive.google.com/file/d/file_id_001/view",
            "webContentLink": None,
        }

        # 테스트 파일 생성
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World", encoding="utf-8")

        uploader = GDriveUploader(gdrive_config)
        result = uploader.upload_file(test_file)

        assert result.file_id == "file_id_001"
        assert result.file_name == "test.txt"
        assert "drive.google.com" in (result.web_view_link or "")

    @patch("xp.gdrive_uploader._get_drive_service")
    def test_upload_directory(self, mock_get_service, gdrive_config, tmp_path):
        """디렉토리 업로드를 테스트합니다."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # _ensure_subfolder is called once per upload_file, each time calling
        # list() then possibly create(). First call: no folder → create.
        # Second call: folder exists → no create.
        list_results = [
            {"files": []},                        # 1st file → subfolder not found
            {"files": [{"id": "subfolder_id"}]},  # 2nd file → subfolder found
        ]
        mock_service.files().list().execute.side_effect = list_results

        create_results = [
            {"id": "subfolder_id"},     # 서브폴더 생성
            {                            # 파일 1 업로드
                "id": "f1",
                "name": "a.txt",
                "webViewLink": "https://link1",
            },
            {                            # 파일 2 업로드
                "id": "f2",
                "name": "b.png",
                "webViewLink": "https://link2",
            },
        ]
        mock_service.files().create().execute.side_effect = create_results

        # 테스트 파일 생성
        (tmp_path / "a.txt").write_text("text", encoding="utf-8")
        (tmp_path / "b.png").write_bytes(b"png-data")

        uploader = GDriveUploader(gdrive_config)
        results = uploader.upload_directory(tmp_path, subfolder="test-sub")

        assert len(results) == 2

    @patch("xp.gdrive_uploader._get_drive_service")
    def test_upload_nonexistent_dir_raises(self, mock_get_service, gdrive_config):
        """존재하지 않는 디렉토리에 대해 에러를 발생시킵니다."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        uploader = GDriveUploader(gdrive_config)
        with pytest.raises(FileNotFoundError):
            uploader.upload_directory(Path("/nonexistent/dir"))


class TestGuessMime:
    @pytest.mark.parametrize(
        "suffix, expected",
        [
            (".png", "image/png"),
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".txt", "text/plain"),
            (".md", "text/markdown"),
            (".json", "application/json"),
            (".xyz", "application/octet-stream"),
        ],
    )
    def test_mime_types(self, suffix, expected):
        assert GDriveUploader._guess_mime(Path(f"file{suffix}")) == expected
