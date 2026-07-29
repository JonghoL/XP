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
from xp.x_poster_browser import BrowserXPoster


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
        # 그 외 모든 셀렉터(작성창/파일입력/버튼)는 존재하는 요소로 취급
        return MagicMock()

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

    def test_missing_textarea_raises(self):
        poster = BrowserXPoster(profile="x", headless=True)
        page = MagicMock()
        page.ele.return_value = None  # 작성창을 못 찾음
        with patch.object(poster, "_open_page", return_value=page):
            with pytest.raises(RuntimeError, match="작성창"):
                poster.post_content(_single(), [])
        page.quit.assert_called_once()  # 실패해도 브라우저는 닫아야 한다

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
