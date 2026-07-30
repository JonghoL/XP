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

# ── X 아티클 작성기 (배너형 롱폼) ──
# 아티클은 공개 API가 없어 웹 작성기를 브라우저로 조작한다. 아래 URL/셀렉터는
# 실측 검증되지 않았으므로 첫 실행 시 조정이 필요할 수 있다. 셀렉터를 못 찾으면
# 해당 단계는 사용자가 열린 브라우저에서 수동으로 처리하고 계속 진행한다.
# 아티클은 SPA라서 허브(/compose/articles)로 먼저 진입한 뒤, '새 아티클' 링크를
# 클릭해 편집기로 들어간다. (직접 /new 진입은 허브로 리다이렉트되는 경우가 있음)
ARTICLE_HUB_URL = "https://x.com/compose/articles"
# '새 아티클'은 <button aria-label="create">. 직접 /new URL은 허브로 리다이렉트되므로
# 반드시 이 버튼을 SPA에서 클릭해야 편집기로 들어간다.
SEL_ART_CREATE = 'css:button[aria-label="create"], a[href="/compose/articles/new"]'
# 편집기 진입 판정용: 제목 textarea 또는 본문 에디터가 나타나면 편집기로 본다.
SEL_ART_EDITOR_READY = (
    'css:textarea[name="Article Title"], .public-DraftEditor-content'
)
# 실측 확정 셀렉터 (X 아티클 편집기, 2026-07 기준):
SEL_ART_TITLE = 'css:textarea[name="Article Title"], textarea[placeholder="Add a title"]'
SEL_ART_BODY = (
    'css:[data-testid="composerRichTextInputContainer"] .public-DraftEditor-content, '
    '.public-DraftEditor-content'
)
SEL_ART_COVER = 'css:input[data-testid="fileInput"], input[type="file"]'
# 커버 업로드 시 뜨는 크롭 모달의 'Apply'(확정) 버튼.
SEL_ART_COVER_APPLY = (
    'xpath://div[@role="dialog"]//button[.//span[normalize-space(text())="Apply"]]'
    ' | //button[.//span[normalize-space(text())="Apply" or '
    'normalize-space(text())="Save" or normalize-space(text())="Done" or '
    'normalize-space(text())="확인"]]'
)
# 편집기 우상단 'Publish' 버튼 -> 게시 설정 다이얼로그의 최종 게시 버튼.
SEL_ART_PUBLISH = 'xpath://button[.//span[contains(text(),"Publish")]]'
# 게시 설정 단계는 role=dialog가 아니라 전체 화면 스텝(Back 버튼 존재)이라
# dialog 조건 없이 'Publish' 텍스트 버튼을 그대로 찾는다.
SEL_ART_PUBLISH_FINAL = 'xpath://button[.//span[normalize-space(text())="Publish"]]'
# 최종 확인 모달(있을 경우)의 확정 버튼.
SEL_ART_CONFIRM = (
    'xpath://div[@role="dialog"]//button[.//span[normalize-space(text())="Publish"]]'
    ' | //button[@data-testid="confirmationSheetConfirm"]'
)


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
    # X 아티클(배너형 롱폼) 발행 — 어시스트 방식
    # ──────────────────────────────────────────

    @staticmethod
    def _react_set_value(el, value: str) -> None:  # noqa: ANN001
        """React 컨트롤드 input/textarea에 값을 넣습니다.

        .value 직접 설정은 React가 감지 못 하므로, 네이티브 value setter로 값을
        넣은 뒤 input/change 이벤트를 디스패치해 React 상태를 갱신시킨다.
        """
        js = """
        const el = this;
        const value = arguments[0];
        const proto = el.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        """
        el.run_js(js, value)

    @staticmethod
    def _draftjs_paste(el, text: str) -> None:  # noqa: ANN001
        """DraftJS 에디터에 텍스트를 넣습니다.

        DraftJS는 insertText/synthetic Ctrl+V를 무시하므로, 텍스트를 실은
        paste 이벤트(ClipboardEvent)를 JS로 직접 디스패치한다. DraftJS의 onPaste
        핸들러가 clipboardData를 읽어 에디터 모델에 반영한다.
        (DrissionPage: 요소의 run_js에서 this는 해당 요소를 가리킨다.)
        """
        js = """
        const el = this;
        const text = arguments[0];
        el.focus();
        const dt = new DataTransfer();
        dt.setData('text/plain', text);
        const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
        el.dispatchEvent(ev);
        """
        el.run_js(js, text)

    @staticmethod
    def _wait_enabled(page, selector: str, timeout: int = 20):  # noqa: ANN001, ANN205
        """버튼이 나타나 '활성' 상태가 될 때까지 기다려 반환합니다(없으면 None)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            btn = page.ele(selector, timeout=2)
            if btn and btn.attr("disabled") is None and btn.attr("aria-disabled") != "true":
                return btn
            time.sleep(1)
        return None

    @staticmethod
    def _url(page) -> str:  # noqa: ANN001
        """현재 페이지 URL을 안전하게 반환합니다."""
        try:
            return page.url or ""
        except Exception:  # noqa: BLE001
            return "(알 수 없음)"

    def _dump_debug(self, page, debug_dir: str | Path | None) -> str:
        """셀렉터를 못 찾았을 때 페이지 HTML/스크린샷을 저장해 진단을 돕습니다."""
        target = Path(debug_dir or ".")
        target.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        html_path = target / f"article-debug-{stamp}.html"
        try:
            html_path.write_text(page.html, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            page.get_screenshot(path=str(target), name=f"article-debug-{stamp}.png")
        except Exception:  # noqa: BLE001
            pass
        return str(html_path)

    def post_article(
        self,
        title: str,
        body: str,
        cover_path: str | Path | None = None,
        *,
        publish: bool = True,
        hold_for_review: bool = False,
        debug_dir: str | Path | None = None,
    ) -> None:
        """X 아티클 작성기를 브라우저로 조작해 아티클을 작성/발행합니다.

        수동 개입 없이 끝까지 자동 진행합니다. 필수 요소(제목/본문/게시 버튼)를
        찾지 못하면 페이지 덤프를 저장하고 명확한 오류를 냅니다(셀렉터 조정용).
        hold_for_review=True면 발행 직전에 한 번만 멈춰 검토를 받습니다.
        """
        page = self._open_page()
        try:
            console.print("[bold cyan]🌐 X 아티클 작성기를 자동 조작합니다...[/]")
            # 허브 진입 후 'create' 버튼을 SPA에서 클릭해 편집기로 들어간다.
            page.get(ARTICLE_HUB_URL)
            time.sleep(3)
            console.print(f"   [dim]허브 URL: {self._url(page)}[/]")

            create_btn = page.ele(SEL_ART_CREATE, timeout=10)
            if not create_btn:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(
                    f"허브에서 '새 아티클(create)' 버튼을 찾지 못했습니다 "
                    f"(URL: {self._url(page)}). 디버그: {dump}"
                )
            create_btn.click()
            time.sleep(3)
            # 아직 편집기로 안 넘어갔으면 JS 클릭으로 재시도.
            if "/new" not in self._url(page):
                try:
                    create_btn.click(by_js=True)
                    time.sleep(3)
                except Exception:  # noqa: BLE001
                    pass
            console.print(f"   [dim]편집기 URL: {self._url(page)}[/]")
            page.ele(SEL_ART_EDITOR_READY, timeout=15)
            time.sleep(2)

            # 제목 (필수)
            el = page.ele(SEL_ART_TITLE, timeout=15)
            if not el:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(
                    f"아티클 '제목' 입력창을 찾지 못했습니다 (현재 URL: {self._url(page)}). "
                    f"편집기 진입 실패 또는 셀렉터 조정 필요. 디버그: {dump}"
                )
            el.click()
            self._react_set_value(el, title)
            console.print("   ✅ 제목 입력")

            # 커버(헤더) 이미지 (선택)
            if cover_path and Path(cover_path).exists():
                fi = page.ele(SEL_ART_COVER, timeout=5)
                if fi:
                    fi.input(str(Path(cover_path)))
                    console.print("   ✅ 커버 이미지 업로드")
                    time.sleep(3)
                    # 커버 크롭 모달의 'Apply' 확정 (안 누르면 모달이 게시를 막는다)
                    apply_btn = page.ele(SEL_ART_COVER_APPLY, timeout=8)
                    if apply_btn:
                        apply_btn.click(by_js=True)
                        console.print("   ✅ 커버 Apply(확정)")
                        time.sleep(2)
                    else:
                        console.print("[yellow]   ⚠️ 커버 Apply 버튼 미발견(모달이 없을 수도).[/]")
                else:
                    console.print("[yellow]   ⚠️ 커버 업로드 요소를 못 찾아 건너뜁니다.[/]")

            # 본문 (필수)
            el = page.ele(SEL_ART_BODY, timeout=10)
            if not el:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(
                    f"아티클 작성기의 '본문' 입력창을 찾지 못했습니다. "
                    f"셀렉터 조정이 필요합니다. 디버그: {dump}"
                )
            # DraftJS는 insertText를 무시하므로 JS paste 이벤트로 본문을 주입한다.
            el.click()
            time.sleep(0.3)
            self._draftjs_paste(el, body)
            time.sleep(1.5)
            try:
                blen = int(el.run_js("return (this.innerText||'').length") or 0)
                console.print(f"   ✅ 본문 입력 — 에디터 {blen}자 / 원문 {len(body)}자")
            except Exception:  # noqa: BLE001
                console.print("   ✅ 본문 입력(paste 이벤트)")

            if hold_for_review and not publish:
                console.print("[yellow]검토 모드: 브라우저에서 확인 후 직접 게시하세요.[/]")
                try:
                    input("   검토를 마쳤으면 Enter로 브라우저를 닫습니다... ")
                except EOFError:
                    time.sleep(60)
                return

            # 발행: 편집기 'Publish' -> 설정화면 'Publish' -> (있으면) 최종 확인
            pub = page.ele(SEL_ART_PUBLISH, timeout=10)
            if not pub:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(f"'Publish' 버튼을 찾지 못했습니다. 디버그: {dump}")
            pub.click(by_js=True)
            console.print("   · 편집기 'Publish' 클릭")
            time.sleep(5)  # 게시 설정 화면 로딩 대기

            final = page.ele(SEL_ART_PUBLISH_FINAL, timeout=10)
            if final:
                final.click(by_js=True)
                console.print("   · 설정화면 'Publish' 클릭")
                time.sleep(4)

            confirm = page.ele(SEL_ART_CONFIRM, timeout=5)
            if confirm:
                confirm.click(by_js=True)
                console.print("   · 최종 확인 클릭")
                time.sleep(3)

            # 발행 검증: 편집 화면 URL을 벗어났는지 확인
            time.sleep(2)
            cur = self._url(page)
            if "compose/articles/edit" in cur or "compose/articles/new" in cur:
                dump = self._dump_debug(page, debug_dir)
                console.print(
                    f"[yellow]⚠️ 아직 편집/설정 화면입니다 (URL: {cur}). 발행이 완료되지 "
                    f"않았을 수 있습니다(커버 확인 등 남은 단계 가능). 디버그: {dump}[/]"
                )
            else:
                console.print(f"[bold green]✅ 아티클 발행 완료 (URL: {cur}).[/]")
        finally:
            page.quit()

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

        if content.post_type in (PostType.SINGLE, PostType.COLUMN):
            if content.tweet is None:
                raise ValueError("단건/칼럼 콘텐츠에 본문이 없습니다.")
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
