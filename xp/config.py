"""XP 설정 관리.

환경변수와 .env 파일에서 설정을 로드합니다.
Grok 인증은 X OAuth(구독 로그인) 전용입니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _find_env_file() -> Path | None:
    """워크스페이스 루트의 .env 파일을 찾습니다."""
    here = Path(__file__).resolve().parent.parent
    env_path = here / ".env"
    if env_path.exists():
        return env_path
    return None


@dataclass(frozen=True)
class XAIConfig:
    """xAI (Grok) API 설정.

    인증은 X OAuth(구독 로그인) 토큰만 사용합니다.
    """

    chat_model: str = "grok-4.5"
    image_model: str = "grok-imagine-image"
    base_url: str = "https://api.x.ai/v1"
    # 콘텐츠 생성 시 web_search/x_search로 최신 사실을 조사할지 여부.
    live_search: bool = True

    def get_access_token(self) -> str:
        """X OAuth access token을 반환합니다.

        저장된 OAuth 토큰을 반환하며, 만료 임박 시 자동 갱신합니다.

        Raises:
            RuntimeError: 로그인 토큰이 없을 때.
        """
        from xp import grok_oauth

        if not grok_oauth.DEFAULT_TOKEN_PATH.exists():
            raise RuntimeError(
                "X OAuth 로그인이 필요합니다.\n"
                "  먼저 로그인하세요: python -m xp grok-login"
            )
        return grok_oauth.get_access_token()


@dataclass(frozen=True)
class GDriveConfig:
    """Google Drive 업로드 설정."""

    sa_key_path: str
    folder_id: str


@dataclass(frozen=True)
class XAPIConfig:
    """X (Twitter) API 설정 - Phase 2."""

    consumer_key: str
    consumer_secret: str
    access_token: str
    access_token_secret: str


@dataclass
class AppConfig:
    """전체 애플리케이션 설정."""

    xai: XAIConfig
    gdrive: GDriveConfig | None = None
    xapi: XAPIConfig | None = None
    output_dir: Path = field(default_factory=lambda: Path("output"))
    # 리서치 md를 넣어두면 `xp column`이 읽어가는 입력 폴더.
    input_dir: Path = field(default_factory=lambda: Path("input"))

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    """환경변수에서 설정을 로드합니다.

    xAI 인증은 X OAuth 로그인(python -m xp grok-login)으로 수행합니다.
    실제 토큰 조회는 API 호출 시점에 XAIConfig.get_access_token()으로 수행합니다.

    Returns:
        AppConfig 인스턴스.
    """
    env_file = _find_env_file()
    if env_file:
        load_dotenv(env_file)

    # -- xAI (모델 설정만, 인증은 호출 시점에 resolve) --
    xai = XAIConfig(
        chat_model=os.getenv("XAI_CHAT_MODEL", "grok-4.5"),
        image_model=os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image"),
        live_search=os.getenv("XP_LIVE_SEARCH", "1").lower()
        not in ("0", "false", "no", "off"),
    )

    # -- Google Drive (선택) --
    gdrive = None
    sa_path = os.getenv("GOOGLE_SA_KEY_PATH")
    folder_id = os.getenv("GDRIVE_FOLDER_ID")
    if sa_path and folder_id:
        gdrive = GDriveConfig(sa_key_path=sa_path, folder_id=folder_id)

    # -- X API (선택, Phase 2) --
    xapi = None
    x_ck = os.getenv("X_CONSUMER_KEY")
    x_cs = os.getenv("X_CONSUMER_SECRET")
    x_at = os.getenv("X_ACCESS_TOKEN")
    x_ats = os.getenv("X_ACCESS_TOKEN_SECRET")
    if all([x_ck, x_cs, x_at, x_ats]):
        xapi = XAPIConfig(
            consumer_key=x_ck,  # type: ignore[arg-type]
            consumer_secret=x_cs,  # type: ignore[arg-type]
            access_token=x_at,  # type: ignore[arg-type]
            access_token_secret=x_ats,  # type: ignore[arg-type]
        )

    output_dir = Path(os.getenv("XP_OUTPUT_DIR", "output"))
    input_dir = Path(os.getenv("XP_INPUT_DIR", "input"))

    return AppConfig(
        xai=xai, gdrive=gdrive, xapi=xapi, output_dir=output_dir, input_dir=input_dir
    )
