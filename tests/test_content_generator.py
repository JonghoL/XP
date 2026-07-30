"""content_generator 테스트."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from xp.config import XAIConfig
from xp.content_generator import ContentGenerator
from xp.models import PostType
from xp.xai_client import extract_json as _extract_json


# ──────────────────────────────────────────────
# _extract_json 테스트
# ──────────────────────────────────────────────


class TestExtractJson:
    def test_pure_json(self):
        text = '{"text": "hello", "hashtags": []}'
        result = _extract_json(text)
        assert result["text"] == "hello"

    def test_code_block_json(self):
        text = '여기 결과입니다:\n```json\n{"text": "world"}\n```\n끝.'
        result = _extract_json(text)
        assert result["text"] == "world"

    def test_code_block_no_lang(self):
        text = '```\n{"text": "test"}\n```'
        result = _extract_json(text)
        assert result["text"] == "test"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="유효한 JSON"):
            _extract_json("이건 JSON이 아닙니다")


# ──────────────────────────────────────────────
# ContentGenerator 테스트 (API 모킹)
# ──────────────────────────────────────────────


@pytest.fixture
def xai_config():
    return XAIConfig()


def _responses_payload(content: dict) -> dict:
    """JSON 콘텐츠를 /responses API 응답 형태로 감쌉니다."""
    return {
        "output": [
            {"type": "web_search_call"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(content, ensure_ascii=False),
                    }
                ],
            },
        ]
    }


@pytest.fixture
def single_api_response():
    """단건 트윗 API 응답 Mock (/responses 형태)."""
    return _responses_payload(
        {
            "text": "AI가 바꾸는 2026년의 일상 🤖",
            "hashtags": ["AI", "2026트렌드", "미래기술"],
            "image_prompt": "Futuristic cityscape with AI robots",
        }
    )


class TestContentGeneratorSingle:
    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_generate_single(self, mock_get_key, mock_post, xai_config, single_api_response):
        mock_post.return_value = single_api_response

        gen = ContentGenerator(xai_config)
        result = gen.generate_single(topic="AI 트렌드", keywords=["인공지능"])

        assert result.post_type == PostType.SINGLE
        assert result.tweet is not None
        assert "AI" in result.tweet.text
        assert len(result.tweet.hashtags) == 3
        assert result.image_prompt is not None
        mock_get_key.assert_called_once()


class TestContentGeneratorColumn:
    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_generate_column(self, mock_get_key, mock_post, xai_config):
        long_text = "서학개미의 시대\n\n" + ("본문 문단입니다. " * 30)
        mock_post.return_value = _responses_payload(
            {
                "title": "서학개미의 시대",
                "text": long_text,
                "image_prompt": "An editorial cartoon of ants marching to Wall Street",
            }
        )

        gen = ContentGenerator(xai_config)
        result = gen.generate_column("## 리서치\n- 순매수 5조원\n- SOXL 집중")

        assert result.post_type == PostType.COLUMN
        assert result.topic == "서학개미의 시대"
        assert result.tweet is not None
        assert len(result.tweet.text) > 280  # 롱폼
        assert result.tweet.hashtags == []  # 칼럼엔 해시태그 없음
        assert result.image_prompt is not None

    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_column_title_override(self, mock_get_key, mock_post, xai_config):
        mock_post.return_value = _responses_payload(
            {"title": "모델제목", "text": "제목\n\n본문", "image_prompt": None}
        )
        gen = ContentGenerator(xai_config)
        result = gen.generate_column("자료", title="내가정한제목")
        assert result.topic == "내가정한제목"


class TestContentGeneratorThread:
    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_generate_thread(self, mock_get_key, mock_post, xai_config):
        mock_post.return_value = _responses_payload(
            {
                "topic": "AI 트렌드",
                "tweets": [
                    {
                        "text": "🧵 2026년 AI 트렌드를 정리합니다.",
                        "hashtags": ["AI", "스레드"],
                        "image_prompt": "AI trend infographic",
                    },
                    {
                        "text": "1. 멀티모달 AI가 대세입니다.",
                        "hashtags": [],
                        "image_prompt": None,
                    },
                    {
                        "text": "이 스레드가 도움이 되셨다면 RT 부탁드립니다! 🙏",
                        "hashtags": [],
                        "image_prompt": None,
                    },
                ],
            }
        )

        gen = ContentGenerator(xai_config)
        result = gen.generate_thread(topic="AI 트렌드")

        assert result.post_type == PostType.THREAD
        assert result.thread is not None
        assert result.thread.tweet_count == 3
        assert result.image_prompt is not None
