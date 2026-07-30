"""Grok Imagine API를 이용한 이미지 생성.

X OAuth(구독 로그인) 토큰으로 인증하여 이미지를 생성하고 로컬에 저장합니다.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
from rich.console import Console

from xp.config import XAIConfig
from xp.models import GeneratedImage

console = Console()

# 우측 하단 워터마크 기본값. XP_WATERMARK 환경변수로 덮어쓰거나 빈 값으로 끌 수 있습니다.
DEFAULT_WATERMARK = "@돈버는코드"

# 한글 워터마크용 폰트 후보 (Windows 맑은 고딕 -> macOS 애플 고딕 -> Linux 나눔고딕).
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\malgunbd.ttf",
    r"C:\Windows\Fonts\malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)


def _parse_ratio(spec: str) -> float:
    """'16:9' 또는 '1.78' 형태를 가로/세로 비율(float)로 변환합니다."""
    spec = spec.strip()
    if ":" in spec:
        w, h = spec.split(":", 1)
        return float(w) / float(h)
    return float(spec)


def fit_aspect_ratio(image_path: Path, ratio: float) -> None:
    """이미지를 목표 가로세로 비율로 중앙 크롭합니다.

    xAI 이미지 API가 사이즈 파라미터를 지원하지 않아, 생성 후 후처리로
    비율을 맞춘다. 워터마크 합성보다 먼저 호출해야 워터마크가 잘리지 않는다.
    """
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    current = width / height
    if abs(current - ratio) < 0.01:
        return

    if current > ratio:
        # 너무 넓음 -> 좌우를 잘라낸다.
        new_w = round(height * ratio)
        left = (width - new_w) // 2
        box = (left, 0, left + new_w, height)
    else:
        # 너무 높음(세로) -> 위아래를 잘라낸다.
        new_h = round(width / ratio)
        top = (height - new_h) // 2
        box = (0, top, width, top + new_h)

    img.crop(box).save(image_path)


def _load_font(size: int):  # noqa: ANN202
    """한글을 지원하는 트루타입 폰트를 로드합니다."""
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def add_watermark(image_path: Path, text: str = DEFAULT_WATERMARK) -> None:
    """이미지 우측 하단에 워터마크를 합성합니다.

    워터마크가 그림 위에 겹치는 것을 전제로, 배경 밝기를 샘플링해 대비되는
    글자색을 고르고 두꺼운 반대색 외곽선을 둘러 복잡한 배경에서도 색이
    묻히지 않고 또렷하게 보이도록 합니다. (배경 박스는 넣지 않습니다.)
    """
    if not text:
        return

    from PIL import Image, ImageDraw

    img = Image.open(image_path).convert("RGB")
    width, height = img.size

    font_size = max(20, int(width * 0.028))
    stroke = max(3, font_size // 8)
    margin = int(width * 0.02)
    font = _load_font(font_size)

    draw = ImageDraw.Draw(img)
    anchor = "rd"  # 오른쪽 아래 기준점
    px, py = width - margin, height - margin

    # 워터마크가 차지할 실제 픽셀 영역을 구해 배경 밝기를 샘플링한다.
    left, top, right, bottom = draw.textbbox(
        (px, py), text, font=font, anchor=anchor, stroke_width=stroke
    )
    region = img.crop(
        (max(0, left), max(0, top), min(width, right), min(height, bottom))
    )
    mean_luma = region.convert("L").resize((1, 1)).getpixel((0, 0))

    if mean_luma >= 128:
        fill, stroke_fill = (20, 20, 20), (255, 255, 255)
    else:
        fill, stroke_fill = (245, 245, 245), (20, 20, 20)

    draw.text(
        (px, py),
        text,
        font=font,
        anchor=anchor,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=stroke_fill,
    )
    img.save(image_path)


def _post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
    *,
    timeout: int = 120,
    retries: int = 3,
) -> dict:
    """JSON POST 요청. 일시적 오류 시 지수 백오프로 재시도."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt + 1 >= retries:
                raise
            time.sleep(1.5 * (2**attempt))
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise RuntimeError(f"API 요청 실패 ({url}): {exc}") from exc
            time.sleep(1.5 * (2**attempt))
    raise RuntimeError(f"API 요청 실패 ({url}): {last_error}")


class ImageGenerator:
    """Grok Imagine API를 사용하여 이미지를 생성합니다.

    인증: X OAuth(구독 로그인) 토큰.
    """

    def __init__(self, config: XAIConfig) -> None:
        self._config = config

    def generate(
        self,
        prompt: str,
        output_dir: Path,
        filename: str = "image.png",
        *,
        n: int = 1,
    ) -> list[GeneratedImage]:
        """이미지를 생성하고 로컬에 저장합니다.

        Args:
            prompt: 이미지 생성 프롬프트 (영어 권장).
            output_dir: 이미지 저장 디렉토리.
            filename: 저장할 파일명 (n > 1이면 숫자 접미사 추가).
            n: 생성할 이미지 수 (1~10).

        Returns:
            GeneratedImage 리스트.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"[bold cyan]🎨 이미지 생성 중...[/] (모델: {self._config.image_model})")

        key = self._config.get_access_token()

        payload = {
            "model": self._config.image_model,
            "prompt": prompt,
            "n": n,
            "response_format": "url",
        }

        url = f"{self._config.base_url}/images/generations"

        try:
            data = _post_json(url, payload, {"Authorization": f"Bearer {key}"})
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            # OAuth 토큰 만료 -> 강제 갱신 후 재시도
            from xp import grok_oauth
            key = grok_oauth.get_access_token(force_refresh=True)
            data = _post_json(url, payload, {"Authorization": f"Bearer {key}"})

        results: list[GeneratedImage] = []
        stem = Path(filename).stem
        suffix = Path(filename).suffix or ".png"
        watermark = os.environ.get("XP_WATERMARK", DEFAULT_WATERMARK)
        aspect = os.environ.get("XP_IMAGE_ASPECT", "16:9")

        for i, img_data in enumerate(data.get("data", [])):
            # 파일명 결정
            if n == 1:
                fname = f"{stem}{suffix}"
            else:
                fname = f"{stem}_{i + 1}{suffix}"
            save_path = output_dir / fname

            # 이미지 다운로드 및 저장
            img_url = img_data.get("url")
            img_b64 = img_data.get("b64_json")

            if img_b64:
                image_bytes = base64.b64decode(img_b64)
                save_path.write_bytes(image_bytes)
            elif img_url:
                self._download_image(img_url, save_path)
            else:
                console.print(f"[bold red]❌ 이미지 데이터를 받지 못했습니다 (index={i})[/]")
                continue

            # 목표 비율로 크롭 (워터마크보다 먼저 실행해 워터마크가 잘리지 않게)
            if aspect:
                try:
                    fit_aspect_ratio(save_path, _parse_ratio(aspect))
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[yellow]⚠️  비율 조정 실패, 건너뜀: {exc}[/]")

            # 우측 하단 워터마크 합성 (실패해도 생성은 계속)
            if watermark:
                try:
                    add_watermark(save_path, watermark)
                    console.print(f"[dim]   🔖 워터마크 추가: {watermark}[/]")
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[yellow]⚠️  워터마크 합성 실패, 건너뜀: {exc}[/]")

            result = GeneratedImage(
                prompt=prompt,
                local_path=save_path,
                model_used=self._config.image_model,
            )
            results.append(result)
            console.print(f"[bold green]✅ 이미지 저장: {save_path}[/]")

        return results

    @staticmethod
    def _download_image(url: str, save_path: Path) -> None:
        """URL에서 이미지를 다운로드합니다."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
