"""Google Drive 파일 업로드.

Service Account 인증으로 Google Drive API v3에 파일을 업로드합니다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console

from xp.config import GDriveConfig
from xp.models import UploadResult

console = Console()


def _get_drive_service(config: GDriveConfig):  # noqa: ANN202
    """Google Drive API 서비스 인스턴스를 생성합니다."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    creds = service_account.Credentials.from_service_account_file(
        config.sa_key_path, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def _ensure_subfolder(
    service, parent_id: str, folder_name: str  # noqa: ANN001
) -> str:
    """하위 폴더가 없으면 생성하고 ID를 반환합니다."""
    query = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{folder_name}' "
        f"and '{parent_id}' in parents "
        f"and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    # 폴더 생성
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


class GDriveUploader:
    """Google Drive에 파일을 업로드합니다."""

    def __init__(self, config: GDriveConfig) -> None:
        self._config = config
        self._service = _get_drive_service(config)

    def upload_file(
        self,
        local_path: Path,
        *,
        subfolder: str | None = None,
        mime_type: str | None = None,
    ) -> UploadResult:
        """단일 파일을 Google Drive에 업로드합니다.

        Args:
            local_path: 업로드할 로컬 파일 경로.
            subfolder: 대상 폴더 하위에 생성할 서브폴더명. None이면 루트 폴더에 업로드.
            mime_type: MIME 타입. None이면 자동 감지.

        Returns:
            UploadResult.
        """
        from googleapiclient.http import MediaFileUpload

        parent_id = self._config.folder_id

        # 서브폴더 처리
        if subfolder:
            parent_id = _ensure_subfolder(self._service, parent_id, subfolder)

        # MIME 타입 자동 감지
        if mime_type is None:
            mime_type = self._guess_mime(local_path)

        file_metadata = {
            "name": local_path.name,
            "parents": [parent_id],
        }
        media = MediaFileUpload(
            str(local_path), mimetype=mime_type, resumable=True
        )

        console.print(f"[bold cyan]📤 업로드 중: {local_path.name}[/]")

        uploaded = (
            self._service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id,name,webViewLink,webContentLink",
            )
            .execute()
        )

        result = UploadResult(
            file_id=uploaded["id"],
            file_name=uploaded["name"],
            web_view_link=uploaded.get("webViewLink"),
            web_content_link=uploaded.get("webContentLink"),
        )

        console.print(f"[bold green]✅ 업로드 완료: {result.file_name}[/]")
        if result.web_view_link:
            console.print(f"   🔗 {result.web_view_link}")

        return result

    def upload_directory(
        self,
        local_dir: Path,
        *,
        subfolder: str | None = None,
    ) -> list[UploadResult]:
        """디렉토리 내 모든 파일을 업로드합니다.

        Args:
            local_dir: 업로드할 로컬 디렉토리.
            subfolder: 대상 서브폴더명. None이면 날짜 기반 자동 생성.

        Returns:
            UploadResult 리스트.
        """
        if not local_dir.is_dir():
            raise FileNotFoundError(f"디렉토리를 찾을 수 없습니다: {local_dir}")

        if subfolder is None:
            subfolder = datetime.now().strftime("XP_%Y-%m-%d")

        results: list[UploadResult] = []
        for file_path in sorted(local_dir.iterdir()):
            if file_path.is_file():
                result = self.upload_file(file_path, subfolder=subfolder)
                results.append(result)

        console.print(
            f"[bold green]📁 {len(results)}개 파일 업로드 완료 → {subfolder}/[/]"
        )
        return results

    @staticmethod
    def _guess_mime(path: Path) -> str:
        """파일 확장자로 MIME 타입을 추정합니다."""
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".json": "application/json",
            ".yaml": "application/x-yaml",
            ".yml": "application/x-yaml",
        }
        return mime_map.get(path.suffix.lower(), "application/octet-stream")
