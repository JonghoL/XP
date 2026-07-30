"""Grok API를 이용한 포스팅 주제 자동 탐색."""

from __future__ import annotations

from rich.console import Console

from xp.config import XAIConfig
from xp.models import SuggestedTopic
from xp.prompts import SYSTEM_PROMPT_TOPICS, build_topic_user_prompt
from xp.xai_client import call_chat, extract_json

console = Console()


class TopicFinder:
    """Grok API로 최신 화제 기반 포스팅 주제를 제안받습니다."""

    def __init__(self, config: XAIConfig) -> None:
        self._config = config

    def suggest(
        self,
        count: int = 5,
        avoid_topics: list[str] | None = None,
        category: str | None = None,
    ) -> list[SuggestedTopic]:
        """지금 화제가 될 만한 주제 `count`개를 제안받습니다."""
        user_prompt = build_topic_user_prompt(count, avoid_topics, category)

        console.print(f"[bold cyan]🔍 최신 화제 조사 + 주제 {count}개 제안 중...[/]")
        raw = call_chat(self._config, SYSTEM_PROMPT_TOPICS, user_prompt)
        data = extract_json(raw)

        topics = [SuggestedTopic(**t) for t in data.get("topics", [])]
        if not topics:
            raise RuntimeError("Grok이 주제를 하나도 제안하지 않았습니다.")

        console.print(f"[bold green]✅ 주제 {len(topics)}개 제안 완료[/]")
        return topics[:count]
