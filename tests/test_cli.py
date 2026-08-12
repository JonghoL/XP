"""cli 헬퍼 테스트."""

from __future__ import annotations

import json
from unittest.mock import patch

from xp.cli import _humanize_content, _recent_topics
from xp.config import AppConfig, XAIConfig
from xp.models import GeneratedContent, GeneratedThread, GeneratedTweet, PostType


class TestRecentTopics:
    def test_empty_when_dir_missing(self, tmp_path):
        assert _recent_topics(tmp_path / "does-not-exist", 20) == []

    def test_empty_when_limit_zero(self, tmp_path):
        (tmp_path / "2026-07-01-a").mkdir()
        assert _recent_topics(tmp_path, 0) == []

    def test_collects_topics_from_meta(self, tmp_path):
        d1 = tmp_path / "2026-07-01-a"
        d1.mkdir()
        (d1 / "meta.json").write_text(
            json.dumps({"topic": "주제 A"}), encoding="utf-8"
        )

        d2 = tmp_path / "2026-07-02-b"
        d2.mkdir()
        (d2 / "meta.json").write_text(
            json.dumps({"topic": "주제 B"}), encoding="utf-8"
        )

        topics = _recent_topics(tmp_path, 20)

        assert set(topics) == {"주제 A", "주제 B"}

    def test_respects_limit(self, tmp_path):
        for i in range(5):
            d = tmp_path / f"2026-07-0{i}-x"
            d.mkdir()
            (d / "meta.json").write_text(
                json.dumps({"topic": f"주제 {i}"}), encoding="utf-8"
            )

        topics = _recent_topics(tmp_path, 2)

        assert len(topics) == 2

    def test_ignores_missing_or_invalid_meta(self, tmp_path):
        d1 = tmp_path / "2026-07-01-no-meta"
        d1.mkdir()

        d2 = tmp_path / "2026-07-02-bad-meta"
        d2.mkdir()
        (d2 / "meta.json").write_text("not json", encoding="utf-8")

        assert _recent_topics(tmp_path, 20) == []


class TestHumanizeContent:
    @staticmethod
    def _config(tmp_path):
        return AppConfig(xai=XAIConfig(), output_dir=tmp_path / "output")

    @patch("xp.content_generator.ContentGenerator.humanize_texts")
    def test_rewrites_single_tweet(self, mock_humanize, tmp_path):
        mock_humanize.return_value = ["다듬어진 본문"]
        content = GeneratedContent(
            post_type=PostType.SINGLE,
            topic="주제",
            tweet=GeneratedTweet(text="AI가 쓴 듯한 원문"),
        )

        _humanize_content(self._config(tmp_path), content)

        assert content.tweet.text == "다듬어진 본문"
        mock_humanize.assert_called_once_with(["AI가 쓴 듯한 원문"])

    @patch("xp.content_generator.ContentGenerator.humanize_texts")
    def test_rewrites_thread_preserving_order(self, mock_humanize, tmp_path):
        mock_humanize.return_value = ["다듬1", "다듬2"]
        content = GeneratedContent(
            post_type=PostType.THREAD,
            topic="주제",
            thread=GeneratedThread(
                topic="주제",
                tweets=[
                    GeneratedTweet(text="원문1"),
                    GeneratedTweet(text="원문2"),
                ],
            ),
        )

        _humanize_content(self._config(tmp_path), content)

        assert [t.text for t in content.thread.tweets] == ["다듬1", "다듬2"]

    @patch("xp.content_generator.ContentGenerator.humanize_texts")
    def test_keeps_original_text_when_humanize_fails(self, mock_humanize, tmp_path):
        mock_humanize.side_effect = RuntimeError("API 오류")
        content = GeneratedContent(
            post_type=PostType.SINGLE,
            topic="주제",
            tweet=GeneratedTweet(text="원문 그대로"),
        )

        _humanize_content(self._config(tmp_path), content)

        assert content.tweet.text == "원문 그대로"
