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

import json
import os
import time
from pathlib import Path

from rich.console import Console

from xp.models import GeneratedContent, PostResult, PostType

console = Console()

DEFAULT_PROFILE = Path.home() / ".xp" / "x_browser"
COMPOSE_URL = "https://x.com/compose/post"
HOME_URL = "https://x.com/home"

# 로그인 세션(쿠키)을 Chromium 프로필과 별개로 저장하는 파일명(프로필 폴더 안).
# 크론(macOS launchd 등 GUI 세션이 없는 환경)에서는 Chromium이 종료 시 쿠키를
# 디스크로 flush하지 못해 프로필 쿠키가 통째로 사라지는 일이 있다(실측: jar 0개).
# 그래서 로그인 성공 시 쿠키를 이 파일로 내보내고, 게시 직전 다시 주입해
# 프로필의 쿠키 영속에 의존하지 않도록 한다.
SESSION_FILE_NAME = "xp_session.json"

# 로그인 상태 판정용 셀렉터. 로그인된 홈에서만 보이는 요소들.
SEL_LOGGED_IN = (
    'css:[data-testid="SideNav_AccountSwitcher_Button"], '
    '[data-testid="AppTabBar_Home_Link"], '
    '[data-testid="SideNav_NewTweet_Button"]'
)
# 로그아웃(랜딩/로그인) 상태 신호.
SEL_LOGGED_OUT = (
    'css:[data-testid="loginButton"], '
    '[data-testid="google_sign_in_container"], '
    'a[href="/login"]'
)


class SessionExpiredError(RuntimeError):
    """브라우저 로그인 세션이 없거나 만료됨.

    재시도로는 복구할 수 없고 사람이 다시 로그인해야 하는 상황이다
    (`python -m xp x-browser-login`). 일시적 실패와 구분해, 재시도/폴백
    로직이 이 예외는 즉시 위로 전달해 재로그인 알림으로 이어지게 한다.
    """

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
            # 기본값을 헤드리스로 둔다. 스케줄(크론/launchd)은 GUI 세션이 없어
            # 창을 띄우는 방식이 불안정하므로, 명시적으로 끄지 않는 한 헤드리스로
            # 동작한다. 최초 수동 로그인(x-browser-login)만 headless=False로 연다.
            headless = os.environ.get("XP_BROWSER_HEADLESS", "1").lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        self._headless = headless
        # 프로필과 별개로 세션 쿠키를 보관할 파일(프로필 폴더 안).
        self._session_file = self._profile / SESSION_FILE_NAME

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
    # 세션(쿠키) 영속 — 프로필 flush에 의존하지 않는다
    # ──────────────────────────────────────────

    def _load_session_cookies(self) -> list | None:
        """저장된 세션 쿠키(list[dict])를 읽습니다(없거나 손상 시 None)."""
        if not self._session_file.exists():
            return None
        try:
            data = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data or None

    def _save_session_cookies(self, page) -> int:  # noqa: ANN001
        """현재 페이지의 x.com 쿠키를 세션 파일로 내보냅니다. 저장 개수 반환."""
        try:
            cookies = page.cookies(all_domains=False, all_info=True)
            data = [dict(c) for c in cookies]
        except Exception:  # noqa: BLE001
            return 0
        try:
            self._session_file.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            return 0
        return len(data)

    def _restore_session(self, page) -> bool:  # noqa: ANN001
        """저장된 세션 쿠키를 브라우저에 주입합니다(성공 시 True).

        쿠키를 주입하려면 먼저 대상 도메인에 접근해 둔다(로그아웃 랜딩이라도 무방).
        """
        cookies = self._load_session_cookies()
        if not cookies:
            return False
        try:
            page.get("https://x.com")
            page.set.cookies(cookies)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _wait_logged_in(page, timeout: int = 15) -> bool:  # noqa: ANN001
        """로그인 상태가 확인되면 True, 로그아웃 신호를 보면 False를 반환합니다."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if page.ele(SEL_LOGGED_IN, timeout=1):
                return True
            if page.ele(SEL_LOGGED_OUT, timeout=1):
                return False
            time.sleep(1)
        return False

    def _ensure_logged_in(self, page, *, debug_dir=None) -> None:  # noqa: ANN001
        """게시 전 로그인 상태를 확인합니다. 세션이 없으면 즉시 실패시킵니다.

        '트윗 작성창을 못 찾음' 같은 모호한 오류 대신, 재로그인이 필요한
        상황임을 SessionExpiredError로 명확히 구분해 위로 올린다.
        """
        page.get(HOME_URL)
        if self._wait_logged_in(page, timeout=15):
            return
        dump = self._dump_debug(page, debug_dir)
        raise SessionExpiredError(
            "X 로그인 세션이 없거나 만료됐습니다. 재로그인이 필요합니다: "
            f"python -m xp x-browser-login (덤프: {dump})"
        )

    # ──────────────────────────────────────────
    # 최초 1회 로그인
    # ──────────────────────────────────────────

    def open_login(self) -> None:
        """브라우저를 열어 사용자가 x.com에 직접 로그인하도록 합니다.

        로그인 세션은 프로필에 저장되어 이후 자동 게시에 재사용됩니다.
        """
        page = self._open_page()
        try:
            # 기존 세션이 있으면 복원해 이미 로그인 상태인지 먼저 확인한다.
            self._restore_session(page)
            page.get(HOME_URL)
            if self._wait_logged_in(page, timeout=8):
                n = self._save_session_cookies(page)
                console.print(
                    f"[bold green]✅ 이미 로그인돼 있습니다. 세션 갱신 완료 "
                    f"(쿠키 {n}개) → {self._session_file}[/]"
                )
                return

            page.get("https://x.com/login")
            console.print(
                "[bold cyan]브라우저에서 X 계정으로 로그인하세요.[/]\n"
                "  2단계 인증까지 완료해 홈 타임라인이 보이면 준비 끝입니다.\n"
                f"  세션 저장 위치: {self._session_file}"
            )
            try:
                input("로그인을 마쳤으면 이 창에서 Enter를 누르세요... ")
            except EOFError:
                # 비대화형 환경 대비: 잠시 대기
                time.sleep(60)

            # 로그인 성공을 실제로 검증한 뒤에만 세션을 저장한다.
            page.get(HOME_URL)
            if not self._wait_logged_in(page, timeout=20):
                console.print(
                    "[bold red]❌ 로그인이 확인되지 않았습니다. 세션을 저장하지 "
                    "않습니다. 로그인(2단계 인증 포함)을 마치고 다시 실행하세요.[/]"
                )
                return
            n = self._save_session_cookies(page)
            if n == 0:
                console.print(
                    "[bold red]❌ 쿠키를 저장하지 못했습니다. 다시 시도하세요.[/]"
                )
                return
            console.print(
                f"[bold green]✅ 로그인 세션 저장 완료 (쿠키 {n}개) → "
                f"{self._session_file}[/]"
            )
        finally:
            page.quit()

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

    def _fill_tweet_box(self, el, text: str) -> None:  # noqa: ANN001
        """트윗 작성창(리치텍스트 에디터)에 본문을 채웁니다.

        DrissionPage의 문자 단위 .input()은 이 에디터(Draft.js 기반)에서
        React 상태 갱신과 경합해 일부 줄이 지워지거나 마지막 줄만 남는 등
        불안정하다(실측: 여러 줄 본문이 마지막 줄만 게시된 사례). 아티클
        본문과 동일하게 paste 이벤트로 한 번에 주입해 이 문제를 피한다.
        입력 후 에디터 글자 수를 원문과 대조해 누락을 감지하면 한 번 재시도한다.
        """
        el.click()
        time.sleep(0.3)
        for attempt in range(2):
            self._draftjs_paste(el, text)
            time.sleep(1.0)
            try:
                length = int(el.run_js("return (this.innerText||'').length") or 0)
            except Exception:  # noqa: BLE001
                length = len(text)  # 길이 확인 불가 시 통과시킨다.
            if length >= len(text) * 0.9:
                return
            console.print(
                f"[yellow]   ⚠️ 본문 입력이 불완전합니다(에디터 {length}자 / "
                f"원문 {len(text)}자). 재시도...[/]"
            )
        raise RuntimeError(
            f"트윗 본문 입력이 불완전합니다(원문 {len(text)}자, 입력 후 {length}자). "
            "게시를 중단합니다."
        )

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
        *,
        debug_dir: str | Path | None = None,
        retries: int = 3,
    ) -> list[PostResult]:
        """콘텐츠 유형에 맞춰 브라우저로 트윗/스레드를 게시합니다.

        일시적 실패(로딩 지연·DOM 경합·네트워크)는 `retries`회까지 백오프
        재시도한다. 세션 만료(SessionExpiredError)는 재시도로 복구되지 않으므로
        즉시 위로 전달한다(재로그인 알림용).
        """
        images = image_paths or []

        if content.post_type in (PostType.SINGLE, PostType.COLUMN):
            if content.tweet is None:
                raise ValueError("단건/칼럼 콘텐츠에 본문이 없습니다.")
            texts = [content.tweet.full_text]
        else:
            if content.thread is None:
                raise ValueError("스레드 콘텐츠에 thread가 없습니다.")
            texts = [t.full_text for t in content.thread.tweets]

        if content.post_type in (PostType.SINGLE, PostType.THREAD):
            reply_text = content.self_reply_text
            if reply_text:
                texts.append(reply_text)

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            page = self._open_page()
            try:
                # 저장된 세션 쿠키를 주입하고, 로그인 상태를 먼저 검증한다.
                self._restore_session(page)
                self._ensure_logged_in(page, debug_dir=debug_dir)
                result = self._compose_and_post(
                    page, texts, images, debug_dir=debug_dir
                )
                # 게시 성공 시 갱신된 세션(회전된 쿠키)을 다시 영속화한다.
                self._save_session_cookies(page)
                return [result]
            except SessionExpiredError:
                # 재로그인이 필요한 상황 — 재시도 무의미, 즉시 전달.
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                console.print(
                    f"[yellow]   ⚠️ 브라우저 게시 시도 {attempt}/{retries} "
                    f"실패: {exc}[/]"
                )
                if attempt < retries:
                    time.sleep(min(5 * attempt, 20))
            finally:
                page.quit()

        assert last_exc is not None
        raise last_exc

    # ──────────────────────────────────────────
    # 내부: 작성 및 게시
    # ──────────────────────────────────────────

    _VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v")

    def _compose_and_post(
        self,
        page,
        texts: list[str],
        image_paths: list[Path],
        *,
        debug_dir: str | Path | None = None,
    ) -> PostResult:
        """컴포저에 텍스트/이미지를 채우고 게시합니다.

        스레드는 컴포저의 '추가(+)'로 이어 붙여 한 번에 게시합니다.
        """
        console.print(f"[bold cyan]🌐 브라우저로 게시 중... ({len(texts)}개 트윗)[/]")
        page.get(COMPOSE_URL)

        first_box = page.ele(SEL_TEXTAREA.format(i=0), timeout=20)
        if not first_box:
            dump = self._dump_debug(page, debug_dir)
            raise RuntimeError(
                f"트윗 작성창을 찾지 못했습니다. 로그인 세션을 확인하세요. 디버그: {dump}"
            )
        self._fill_tweet_box(first_box, texts[0])

        # 첫 트윗에 이미지/영상 첨부
        valid = [p for p in image_paths if Path(p).exists()]
        has_video = any(str(p).lower().endswith(self._VIDEO_EXTS) for p in valid)
        if valid:
            file_input = page.ele(SEL_FILE_INPUT, timeout=10)
            if file_input:
                file_input.input("\n".join(str(Path(p)) for p in valid))
                kind = "영상" if has_video else "이미지"
                console.print(f"   🖼️  {kind} {len(valid)}개 첨부 — 업로드 대기 중...")
                self._wait_media_ready(page)

        # 스레드: 나머지 트윗을 추가
        for i, text in enumerate(texts[1:], start=1):
            add_btn = page.ele(SEL_ADD_BUTTON, timeout=10)
            if not add_btn:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(f"스레드 추가 버튼을 찾지 못했습니다. 디버그: {dump}")
            add_btn.click()
            box = page.ele(SEL_TEXTAREA.format(i=i), timeout=10)
            if not box:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(f"{i + 1}번째 작성창을 찾지 못했습니다. 디버그: {dump}")
            self._fill_tweet_box(box, text)

        # 미디어 업로드/처리가 끝나기 전까지 X는 게시 버튼을 비활성 상태로 둔다.
        # 활성화될 때까지 기다리지 않고 바로 클릭하면 아무 반응 없이 게시가
        # 누락되므로(업로드 중 "먹통"처럼 보이는 원인), 반드시 활성 대기 후 클릭한다.
        button_timeout = (240 if has_video else 60) if valid else 20
        post_btn = self._wait_enabled(page, SEL_POST_BUTTON, timeout=button_timeout)
        if not post_btn:
            dump = self._dump_debug(page, debug_dir)
            raise RuntimeError(
                "게시 버튼이 비활성 상태로 남아 있습니다(미디어 업로드/처리가 "
                f"끝나지 않았을 수 있음). 디버그: {dump}"
            )

        # 마지막 안전장치: 미디어 업로드/버튼 대기 중 리렌더링으로 본문이
        # 유실됐을 가능성에 대비해, 클릭 직전 모든 작성창을 다시 조회해
        # 원문과 대조한다. 유실이 확인되면 재입력을 한 번 더 시도하고,
        # 그래도 안 맞으면 게시 없이 중단한다(빈 트윗이 올라가는 것을 방지).
        for i, expected in enumerate(texts):
            box = page.ele(SEL_TEXTAREA.format(i=i), timeout=10)
            if not box:
                dump = self._dump_debug(page, debug_dir)
                raise RuntimeError(
                    f"게시 직전 확인 단계에서 {i + 1}번째 작성창을 찾지 못했습니다. "
                    f"디버그: {dump}"
                )
            try:
                length = int(box.run_js("return (this.innerText||'').length") or 0)
            except Exception:  # noqa: BLE001
                length = len(expected)
            if length < len(expected) * 0.9:
                console.print(
                    f"[yellow]   ⚠️ 게시 직전 확인: {i + 1}번째 본문 유실 감지 "
                    f"(원문 {len(expected)}자 / 현재 {length}자). 재입력 시도...[/]"
                )
                self._fill_tweet_box(box, expected)
                length = int(box.run_js("return (this.innerText||'').length") or 0)
                if length < len(expected) * 0.9:
                    dump = self._dump_debug(page, debug_dir)
                    raise RuntimeError(
                        f"{i + 1}번째 본문이 계속 유실되어 게시를 중단합니다"
                        f"(원문 {len(expected)}자 / 현재 {length}자). 디버그: {dump}"
                    )

        post_btn.click(by_js=True)

        confirm_timeout = 60 if has_video else 30
        url = self._wait_confirmation(page, timeout=confirm_timeout, debug_dir=debug_dir)
        tweet_id = url.rstrip("/").split("/")[-1] if "/status/" in url else "unknown"
        console.print(f"[bold green]✅ 브라우저 게시 완료:[/] {url}")
        return PostResult(tweet_id=tweet_id, text=texts[0], url=url)

    @staticmethod
    def _wait_media_ready(page, timeout: int = 60) -> None:
        """미디어 미리보기가 컴포저에 나타날 때까지 대기합니다.

        이 시점엔 업로드가 진행 중일 뿐 완료는 아니다(단순 sanity 체크).
        실제 업로드/처리 완료 여부는 게시 버튼 활성화(`_wait_enabled`)로 판단한다.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if page.ele('css:div[data-testid="attachments"]', timeout=1):
                return
            time.sleep(1)

    def _wait_confirmation(
        self, page, timeout: int = 30, debug_dir: str | Path | None = None
    ) -> str:
        """게시 확인 신호를 기다려 게시물 URL을 최대한 알아냅니다.

        토스트가 가장 확실한 신호지만, 폴링 사이 짧게 떴다 사라지면 놓칠 수
        있다. 그런 경우를 대비해 URL이 컴포저를 벗어났는지 / 작성창이
        사라졌는지도 보조 신호로 함께 확인한다.
        """
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
            cur = self._url(page)
            if "/compose/" not in cur:
                return cur
            if not page.ele(SEL_TEXTAREA.format(i=0), timeout=1):
                return "https://x.com/home"
            time.sleep(0.5)
        dump = self._dump_debug(page, debug_dir)
        raise RuntimeError(
            f"게시 확인을 받지 못했습니다. 실제 게시 여부를 직접 확인하세요. 디버그: {dump}"
        )
