"""article 명령 테스트 (제목/본문 분리)."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

from xp.cli import cmd_article


def _make_column_dir(tmp_path):
    d = tmp_path / "col"
    d.mkdir()
    meta = {
        "post_type": "column",
        "topic": "성장의 칼날과 안정의 무게",
        "tweet": {
            "text": "성장의 칼날과 안정의 무게\n\n본문 첫 문단.\n\n두 번째 문단.",
            "hashtags": [],
            "image_prompt": "an editorial cartoon",
        },
    }
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    (d / "post_image.png").write_bytes(b"fake")
    return d


def test_article_splits_title_and_body(tmp_path):
    d = _make_column_dir(tmp_path)
    with patch("xp.cli._copy_to_clipboard", return_value=True) as clip:
        cmd_article(argparse.Namespace(dir=str(d), post=False, review=False))

    body = (d / "body.txt").read_text(encoding="utf-8")
    # 제목 줄은 본문에서 빠지고, 본문 문단만 남아야 한다.
    assert body.startswith("본문 첫 문단.")
    assert "성장의 칼날과 안정의 무게" not in body
    # 클립보드로 복사된 내용도 본문이어야 한다.
    assert clip.call_args.args[0] == body
