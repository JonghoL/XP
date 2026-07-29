"""x_poster 테스트 (tweepy 모킹)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from xp.config import XAPIConfig
from xp.models import (
    GeneratedContent,
    GeneratedThread,
    GeneratedTweet,
    PostType,
)
from xp.x_poster import XPoster


@pytest.fixture
def xapi_config():
    return XAPIConfig(
        consumer_key="ck",
        consumer_secret="cs",
        access_token="at",
        access_token_secret="ats",
    )


def _fake_v2(tweet_ids):
    """create_tweet가 순차적으로 tweet_ids를 반환하는 Mock 클라이언트."""
    client = MagicMock()
    responses = [MagicMock(data={"id": tid}) for tid in tweet_ids]
    client.create_tweet.side_effect = responses
    return client


class TestPostSingle:
    def test_post_single_no_image(self, xapi_config):
        content = GeneratedContent(
            post_type=PostType.SINGLE,
            topic="t",
            tweet=GeneratedTweet(text="hello", hashtags=["ai"]),
        )
        poster = XPoster(xapi_config)
        with patch.object(poster, "_client_v2", return_value=_fake_v2([123])):
            results = poster.post_content(content, [])

        assert len(results) == 1
        assert results[0].tweet_id == "123"
        assert results[0].url.endswith("/123")
        assert "hello" in results[0].text
        assert results[0].media_ids == []


class TestPostThread:
    def test_thread_chains_replies(self, xapi_config):
        content = GeneratedContent(
            post_type=PostType.THREAD,
            topic="t",
            thread=GeneratedThread(
                topic="t",
                tweets=[
                    GeneratedTweet(text="one"),
                    GeneratedTweet(text="two"),
                    GeneratedTweet(text="three"),
                ],
            ),
        )
        v2 = _fake_v2([1, 2, 3])
        poster = XPoster(xapi_config)
        with patch.object(poster, "_client_v2", return_value=v2):
            results = poster.post_content(content, [])

        assert [r.tweet_id for r in results] == ["1", "2", "3"]
        # 2번째/3번째 트윗은 직전 트윗에 대한 답글이어야 한다.
        calls = v2.create_tweet.call_args_list
        assert "in_reply_to_tweet_id" not in calls[0].kwargs
        assert calls[1].kwargs["in_reply_to_tweet_id"] == "1"
        assert calls[2].kwargs["in_reply_to_tweet_id"] == "2"


class TestMediaUpload:
    def test_single_uploads_image(self, xapi_config, tmp_path):
        img = tmp_path / "post_image.png"
        img.write_bytes(b"fake-png")

        content = GeneratedContent(
            post_type=PostType.SINGLE,
            topic="t",
            tweet=GeneratedTweet(text="pic"),
        )

        v2 = _fake_v2([999])
        v1 = MagicMock()
        v1.media_upload.return_value = MagicMock(media_id=555)

        poster = XPoster(xapi_config)
        with patch.object(poster, "_client_v2", return_value=v2), patch.object(
            poster, "_api_v1", return_value=v1
        ):
            results = poster.post_content(content, [img])

        v1.media_upload.assert_called_once()
        assert results[0].media_ids == ["555"]
        assert v2.create_tweet.call_args.kwargs["media_ids"] == ["555"]

    def test_missing_image_skipped(self, xapi_config, tmp_path):
        missing = tmp_path / "nope.png"
        content = GeneratedContent(
            post_type=PostType.SINGLE,
            topic="t",
            tweet=GeneratedTweet(text="x"),
        )
        v2 = _fake_v2([1])
        v1 = MagicMock()
        poster = XPoster(xapi_config)
        with patch.object(poster, "_client_v2", return_value=v2), patch.object(
            poster, "_api_v1", return_value=v1
        ):
            results = poster.post_content(content, [missing])

        v1.media_upload.assert_not_called()
        assert results[0].media_ids == []
