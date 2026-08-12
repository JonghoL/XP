"""x_poster_browser 테스트 (DrissionPage 완전 모킹).

실제 브라우저/네트워크 없이, 게시 흐름과 셀렉터 사용을 검증한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from xp.models import (
    GeneratedContent,
    GeneratedThread,
    GeneratedTweet,
    PostType,
)
from xp.x_poster_browser import BrowserXPoster, SessionExpiredError


def _fake_page():
    """게시 성공 시나리오를 흉내내는 가짜 ChromiumPage."""
    page = MagicMock()

    # 토스트 안의 링크가 게시물 URL을 돌려준다.
    link = MagicMock()
    link.attr.return_value = "/doncode_/status/123456789"
    toast = MagicMock()
    toast.ele.return_value = link

    def ele(selector, timeout=None):
        if "toast" in selector:
            return toast
        # 그 외 모든 셀렉터(로그인 신호/작성창/파일입력/버튼)는 활성 요소로 취급
        el = MagicMock()
        el.attr.return_value = None
        el.run_js.return_value = 10_000  # 본문 입력 길이 체크를 항상 통과시킴
        return el

    page.ele.side_effect = ele
    return page


def _single():
    return GeneratedContent(
        post_type=PostType.SINGLE,
        topic="t",
        tweet=GeneratedTweet(text="비상 게시 테스트"),
    )


class TestBrowserPost:
    def test_single_post_returns_result(self):
        poster = BrowserXPoster(profile="x", headless=True)
        page = _fake_page()
        with patch.object(poster, "_open_page", return_value=page):
            results = poster.post_content(_single(), [])

        assert len(results) == 1
        assert results[0].tweet_id == "123456789"
        assert results[0].url.endswith("/status/123456789")
        assert "비상 게시" in results[0].text
        page.quit.assert_called_once()

    def test_not_logged_in_raises_session_expired(self):
        # 로그인/로그아웃 신호가 전혀 없는 페이지 = 세션 없음으로 판정.
        # 유료 API 폴백 대신 SessionExpiredError로 명확히 구분해 올린다.
        poster = BrowserXPoster(profile="x", headless=True)
        page = MagicMock()
        page.ele.return_value = None
        with patch.object(poster, "_open_page", return_value=page):
            with pytest.raises(SessionExpiredError, match="세션"):
                poster.post_content(_single(), [])
        # 세션 만료는 재시도하지 않으므로 브라우저는 정확히 한 번 닫힌다.
        page.quit.assert_called_once()

    def test_logged_in_missing_textarea_raises(self):
        # 로그인은 돼 있으나 작성창을 못 찾는 경우 → 작성창 오류로 실패.
        def ele(selector, timeout=None):
            if any(k in selector for k in ("SideNav", "AppTabBar", "NewTweet")):
                return MagicMock()  # 로그인 신호는 있음
            return None  # 작성창 등은 못 찾음

        page = MagicMock()
        page.ele.side_effect = ele
        poster = BrowserXPoster(profile="x", headless=True)
        with patch.object(poster, "_open_page", return_value=page):
            with pytest.raises(RuntimeError, match="작성창"):
                poster.post_content(_single(), [], retries=1)
        page.quit.assert_called_once()

    def test_thread_uses_add_button(self):
        content = GeneratedContent(
            post_type=PostType.THREAD,
            topic="t",
            thread=GeneratedThread(
                topic="t",
                tweets=[GeneratedTweet(text="1"), GeneratedTweet(text="2")],
            ),
        )
        poster = BrowserXPoster(profile="x", headless=True)
        page = _fake_page()
        with patch.object(poster, "_open_page", return_value=page):
            results = poster.post_content(content, [])

        # addButton 셀렉터가 최소 한 번은 조회되어야 한다 (스레드 추가).
        selectors = [c.args[0] for c in page.ele.call_args_list]
        assert any("addButton" in s for s in selectors)
        assert len(results) == 1

    def test_missing_drissionpage_message(self):
        """DrissionPage 미설치 시 친절한 에러."""
        poster = BrowserXPoster(profile="x")
        with patch.dict("sys.modules", {"DrissionPage": None}):
            with pytest.raises(RuntimeError, match="DrissionPage"):
                poster._open_page()


class TestStatusPermalink:
    @staticmethod
    def _toast(href):
        toast = MagicMock()
        if href is None:
            toast.ele.return_value = None
        else:
            link = MagicMock()
            link.attr.return_value = href
            toast.ele.return_value = link
        return toast

    def test_relative_status_href_becomes_full_permalink(self):
        toast = self._toast("/doncode_/status/123")
        assert (
            BrowserXPoster._status_permalink(toast)
            == "https://x.com/doncode_/status/123"
        )

    def test_absolute_status_href_passthrough(self):
        toast = self._toast("https://x.com/u/status/999")
        assert BrowserXPoster._status_permalink(toast) == "https://x.com/u/status/999"

    def test_non_status_link_ignored(self):
        # 'View'가 아닌 다른 링크(설정/도움말 등)는 퍼머링크로 인정하지 않는다.
        toast = self._toast("/settings")
        assert BrowserXPoster._status_permalink(toast) is None

    def test_no_link_returns_none(self):
        toast = self._toast(None)
        assert BrowserXPoster._status_permalink(toast) is None
