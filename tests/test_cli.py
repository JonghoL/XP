"""cli 헬퍼 테스트."""

from __future__ import annotations

import json

from xp.cli import _recent_topics


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
