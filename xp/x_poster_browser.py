"""X(Twitter) 브라우저 자동화 게시 — 비상/장애 대응용.

X API가 막히거나(레이트리밋·쿼터 소진·장애) 사용할 수 없을 때의 폴백 경로.
DrissionPage로 실제 Chromium을 구동해 x.com 웹 UI로 트윗을 게시한다.

인증: 전용 브라우저 프로필에 저장된 로그인 세션을 재사용한다.
      최초 1회 `python -m xp x-browser-login` 으로 수동 로그인해 세션을 만든 뒤
      이후에는 세션(쿠키)으로 자동 게시한다. 비밀번호를 저장하지 않는다.

주의: 웹 UI 자동화는 X의 DOM 변경/봇 탐지에 취약하다. 정상 경로는 API이며,
      이것은 어디까지나 장애 시 임시 대응용이다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from rich.console import Console

from xp.models import GeneratedContent, PostResult, PostType

console = Console()

DEFAULT_PROFILE = Path.home() / ".xp" / "x_browser"
COMPOSE_URL = "https://x.com/compose/post"

# x.com 웹 UI 셀렉터 (data-testid 기반). DOM 변경 시 여기만 고치면 된다.
SEL_TEXTAREA = 'css:div[data-testid="tweetTextarea_{i}"]'
SEL_FILE_INPUT = 'css:input[data-testid="fileInput"]'
SEL_POST_BUTTON = 'css:button[data-testid="tweetButton"]'
SEL_ADD_BUTTON = 'css:button[data-testid="addButton"]'
SEL_TOAST = 'css:div[data-testid="toast"]'


class BrowserXPoster:
    """DrissionPage로 x.com 웹 UI를 구동해 트윗을 게시합니다 (비상용).

    API 기반 XPoster와 동일한 post_content 시그니처를 제공하므로 폴백으로
    그대로 교체할 수 있습니다.
    """

    def __init__(
        self,
        profile: str | Path | None = None,
        *,
        headless: bool | None = None,
    ) -> None:
        self._profile = Path(profile or os.environ.get("XP_BROWSER_PROFILE") or DEFAULT_PROFILE)
        if headless is None:
            headless = os.environ.get("XP_BROWSER_HEADLESS", "0").lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        self._headless = headless

    # ──────────────────────────────────────────
    # 브라우저 수명 관리
    # ──────────────────────────────────────────

    def _open_page(self):  # noqa: ANN202
        """프로필 세션을 사용하는 ChromiumPage를 엽니다."""
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError(
                "DrissionPage가 필요합니다. 설치: pip install -e \".[browser]\""
            ) from exc

        self._profile.mkdir(parents=True, exist_ok=True)
        options = ChromiumOptions()
        options.set_user_data_path(str(self._profile))
        if self._headless:
            options.headless()
        return ChromiumPage(options)

    # ──────────────────────────────────────────
    # 최초 1회 로그인
    # ──────────────────────────────────────────

    def open_login(self) -> None:
        """브라우저를 열어 사용자가 x.com에 직접 로그인하도록 합니다.

        로그인 세션은 프로필에 저장되어 이후 자동 게시에 재사용됩니다.
        """
        page = self._open_page()
        page.get("https://x.com/login")
        console.print(
            "[bold cyan]브라우저에서 X 계정으로 로그인하세요.[/]\n"
            "  2단계 인증까지 완료해 홈 타임라인이 보이면 준비 끝입니다.\n"
            f"  세션 저장 위치: {self._profile}"
        )
        try:
            input("로그인을 마쳤으면 이 창에서 Enter를 누르세요... ")
        except EOFError:
            # 비대화형 환경 대비: 잠시 대기
            time.sleep(60)
        page.quit()
        console.print("[bold green]✅ 로그인 세션 저장 완료.[/]")

    # ──────────────────────────────────────────
    # 공개 메서드 (XPoster와 동일 시그니처)
    # ──────────────────────────────────────────

    def post_content(
        self,
        content: GeneratedContent,
        image_paths: list[Path] | None = None,
    ) -> list[PostResult]:
        """콘텐츠 유형에 맞춰 브라우저로 트윗/스레드를 게시합니다."""
        images = image_paths or []

        if content.post_type == PostType.SINGLE:
            if content.tweet is None:
                raise ValueError("단건 콘텐츠에 tweet이 없습니다.")
            texts = [content.tweet.full_text]
        else:
            if content.thread is None:
                raise ValueError("스레드 콘텐츠에 thread가 없습니다.")
            texts = [t.full_text for t in content.thread.tweets]

        page = self._open_page()
        try:
            result = self._compose_and_post(page, texts, images)
        finally:
            page.quit()
        return [result]

    # ──────────────────────────────────────────
    # 내부: 작성 및 게시
    # ──────────────────────────────────────────

    def _compose_and_post(
        self, page, texts: list[str], image_paths: list[Path]
    ) -> PostResult:
        """컴포저에 텍스트/이미지를 채우고 게시합니다.

        스레드는 컴포저의 '추가(+)'로 이어 붙여 한 번에 게시합니다.
        """
        console.print(f"[bold cyan]🌐 브라우저로 게시 중... ({len(texts)}개 트윗)[/]")
        page.get(COMPOSE_URL)

        first_box = page.ele(SEL_TEXTAREA.format(i=0), timeout=20)
        if not first_box:
            raise RuntimeError("트윗 작성창을 찾지 못했습니다. 로그인 세션을 확인하세요.")
        first_box.input(texts[0])

        # 첫 트윗에 이미지 첨부
        valid = [p for p in image_paths if Path(p).exists()]
        if valid:
            file_input = page.ele(SEL_FILE_INPUT, timeout=10)
            if file_input:
                file_input.input("\n".join(str(Path(p)) for p in valid))
                console.print(f"   🖼️  이미지 {len(valid)}장 첨부")
                self._wait_media_ready(page)

        # 스레드: 나머지 트윗을 추가
        for i, text in enumerate(texts[1:], start=1):
            add_btn = page.ele(SEL_ADD_BUTTON, timeout=10)
            if not add_btn:
                raise RuntimeError("스레드 추가 버튼을 찾지 못했습니다.")
            add_btn.click()
            box = page.ele(SEL_TEXTAREA.format(i=i), timeout=10)
            if not box:
                raise RuntimeError(f"{i + 1}번째 작성창을 찾지 못했습니다.")
            box.input(text)

        post_btn = page.ele(SEL_POST_BUTTON, timeout=10)
        if not post_btn:
            raise RuntimeError("게시 버튼을 찾지 못했습니다.")
        post_btn.click()

        url = self._wait_confirmation(page)
        tweet_id = url.rstrip("/").split("/")[-1] if "/status/" in url else "unknown"
        console.print(f"[bold green]✅ 브라우저 게시 완료:[/] {url}")
        return PostResult(tweet_id=tweet_id, text=texts[0], url=url)

    @staticmethod
    def _wait_media_ready(page, timeout: int = 60) -> None:
        """이미지 업로드(썸네일 렌더링)가 끝날 때까지 대기합니다."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if page.ele('css:div[data-testid="attachments"]', timeout=1):
                return
            time.sleep(1)

    def _wait_confirmation(self, page, timeout: int = 30) -> str:
        """게시 확인 토스트를 기다려 게시물 URL을 최대한 알아냅니다."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            toast = page.ele(SEL_TOAST, timeout=1)
            if toast:
                link = toast.ele("css:a", timeout=1)
                if link:
                    href = link.attr("href") or ""
                    if href:
                        return href if href.startswith("http") else f"https://x.com{href}"
                # 토스트는 떴지만 링크가 없으면 게시는 된 것으로 본다.
                return "https://x.com/home"
            time.sleep(1)
        raise RuntimeError("게시 확인(토스트)을 받지 못했습니다. 실제 게시 여부를 확인하세요.")
