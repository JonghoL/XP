"""Grok Image-to-Video API를 이용한 영상 생성.

생성된 최종 이미지를 입력으로 받아 짧은 영상을 만들고 로컬에 저장합니다.
인증은 이미지 생성과 동일하게 X OAuth(구독 로그인) 토큰을 사용합니다.

이 엔드포인트는 xAI 공식 문서(docs.x.ai)에는 없지만, 실제 호출로 다음 동작을
직접 확인했습니다 (2026-08-08 기준):

1. POST /v1/videos/generations
   body: {"model": ..., "prompt": ..., "image": {"url": "data:<mime>;base64,..."},
          "response_format": "url"}
   -> 즉시 {"request_id": "..."} 반환 (비동기 작업 접수).

2. GET /v1/videos/{request_id}
   -> {"status": "queued"|"in_progress"|"done"|..., "progress": 0-100,
       "video": {"url": "...", "duration": <sec>}} 형태로 폴링.
   완료 전에는 "video" 키가 없을 수 있어 status=="done"이 될 때까지 반복 조회한다.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
from pathlib import Path

import httpx
from rich.console import Console

from xp.config import XAIConfig
from xp.image_generator import _post_json
from xp.models import GeneratedVideo

console = Console()

# 영상 생성은 이미지보다 오래 걸리므로 별도로 넉넉하게 잡는다.
DEFAULT_POLL_INTERVAL = 5
DEFAULT_MAX_WAIT = 480


class VideoGenerator:
    """Grok Image-to-Video API를 사용하여 이미지를 영상으로 변환합니다."""

    def __init__(self, config: XAIConfig) -> None:
        self._config = config

    def generate_from_image(
        self,
        image_path: Path,
        prompt: str,
        output_dir: Path,
        filename: str = "post_video.mp4",
        *,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        max_wait: int = DEFAULT_MAX_WAIT,
    ) -> GeneratedVideo:
        """이미지 한 장을 입력으로 영상을 생성하고 로컬에 저장합니다.

        Args:
            image_path: 입력 이미지(최종 완성 이미지) 경로.
            prompt: 영상의 움직임/장면을 설명하는 프롬프트 (영어 권장).
            output_dir: 영상 저장 디렉토리.
            filename: 저장할 파일명.
            poll_interval: 비동기 생성 시 상태 확인 간격(초).
            max_wait: 비동기 생성 완료를 기다리는 최대 시간(초).

        Returns:
            GeneratedVideo.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        save_path = output_dir / filename

        console.print(
            f"[bold cyan]🎬 이미지 → 영상 변환 중...[/] (모델: {self._config.video_model})"
        )

        key = self._config.get_access_token()
        payload = self._build_payload(image_path, prompt)
        url = f"{self._config.base_url}/videos/generations"
        headers = {"Authorization": f"Bearer {key}"}

        try:
            data = _post_json(url, payload, headers)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            from xp import grok_oauth

            key = grok_oauth.get_access_token(force_refresh=True)
            headers = {"Authorization": f"Bearer {key}"}
            data = _post_json(url, payload, headers)

        request_id = data.get("request_id")
        if not request_id:
            raise RuntimeError(f"영상 생성 응답에 request_id가 없습니다: {data}")

        result = self._poll(request_id, headers, poll_interval, max_wait)
        video_url = (result.get("video") or {}).get("url")
        if not video_url:
            raise RuntimeError(f"완료된 영상 응답에 URL이 없습니다: {result}")

        self._download(video_url, save_path)
        console.print(f"[bold green]✅ 영상 저장: {save_path}[/]")

        return GeneratedVideo(
            prompt=prompt,
            local_path=save_path,
            source_image=image_path,
            model_used=self._config.video_model,
        )

    # ──────────────────────────────────────────
    # 내부 메서드
    # ──────────────────────────────────────────

    def _build_payload(self, image_path: Path, prompt: str) -> dict:
        """요청 페이로드를 구성합니다.

        입력 이미지는 data URI(base64)로 인코딩해 `image.url` 필드에 담는다.
        """
        mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return {
            "model": self._config.video_model,
            "prompt": prompt,
            "image": {"url": f"data:{mime};base64,{b64}"},
            "response_format": "url",
        }

    def _poll(
        self, request_id: str, headers: dict[str, str], poll_interval: int, max_wait: int
    ) -> dict:
        """비동기 영상 생성 작업 상태를 완료(status=="done")될 때까지 폴링합니다."""
        import urllib.request

        status_url = f"{self._config.base_url}/videos/{request_id}"
        deadline = time.monotonic() + max_wait

        while time.monotonic() < deadline:
            request = urllib.request.Request(status_url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))

            status = str(data.get("status", "")).lower()
            if status == "done" or data.get("video"):
                return data
            if status in ("failed", "error"):
                raise RuntimeError(f"영상 생성 실패: {data}")

            progress = data.get("progress")
            suffix = f", 진행률 {progress}%" if progress is not None else ""
            console.print(f"[dim]   ⏳ 영상 생성 대기 중... (상태: {status or '알 수 없음'}{suffix})[/]")
            time.sleep(poll_interval)

        raise RuntimeError(f"영상 생성 대기 시간 초과 ({max_wait}초): request_id={request_id}")

    @staticmethod
    def _download(url: str, save_path: Path) -> None:
        """URL에서 영상을 다운로드합니다."""
        with httpx.Client(timeout=120.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
