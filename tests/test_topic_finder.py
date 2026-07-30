"""topic_finder 테스트."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from xp.config import XAIConfig
from xp.topic_finder import TopicFinder


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


class TestTopicFinder:
    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_suggest_returns_topics(self, mock_get_key, mock_post, xai_config):
        mock_post.return_value = _responses_payload(
            {
                "topics": [
                    {
                        "topic": "A사, 신형 AI 칩 발표",
                        "keywords": ["AI", "반도체"],
                        "reason": "오늘 발표된 최신 이슈",
                    },
                    {
                        "topic": "B리그 결승전 결과",
                        "keywords": ["스포츠"],
                        "reason": "실시간 화제",
                    },
                ]
            }
        )

        finder = TopicFinder(xai_config)
        topics = finder.suggest(count=2)

        assert len(topics) == 2
        assert topics[0].topic == "A사, 신형 AI 칩 발표"
        assert topics[0].keywords == ["AI", "반도체"]
        mock_get_key.assert_called_once()

    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_suggest_truncates_to_count(self, mock_get_key, mock_post, xai_config):
        mock_post.return_value = _responses_payload(
            {
                "topics": [
                    {"topic": f"주제 {i}", "keywords": [], "reason": None}
                    for i in range(5)
                ]
            }
        )

        finder = TopicFinder(xai_config)
        topics = finder.suggest(count=2)

        assert len(topics) == 2

    @patch("xp.xai_client._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_suggest_raises_on_empty(self, mock_get_key, mock_post, xai_config):
        mock_post.return_value = _responses_payload({"topics": []})

        finder = TopicFinder(xai_config)
        with pytest.raises(RuntimeError, match="주제를 하나도 제안"):
            finder.suggest(count=3)
