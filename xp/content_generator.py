"""Grok API를 이용한 X 포스팅 콘텐츠 생성.

X OAuth(구독 로그인) 토큰으로 xAI의 Grok 모델에 접근합니다.
Agent Tools API(/responses)의 web_search·x_search로 최신 사실을 조사한 뒤
콘텐츠를 생성합니다. 토큰 만료(401) 시 강제 갱신 후 재시도합니다.
"""

from __future__ import annotations

from rich.console import Console

from xp.config import XAIConfig
from xp.models import (
    GeneratedContent,
    GeneratedThread,
    GeneratedTweet,
    PostType,
)
from xp.prompts import (
    SYSTEM_PROMPT_COLUMN,
    SYSTEM_PROMPT_SINGLE,
    SYSTEM_PROMPT_THREAD,
    build_column_user_prompt,
    build_user_prompt,
)
from xp.xai_client import call_chat, extract_json

console = Console()


class ContentGenerator:
    """Grok API를 사용하여 X 포스팅 콘텐츠를 생성합니다.

    인증: X OAuth(구독 로그인) 토큰.
    """

    def __init__(self, config: XAIConfig) -> None:
        self._config = config

    # ──────────────────────────────────────────
    # 공개 메서드
    # ──────────────────────────────────────────

    def generate_single(
        self,
        topic: str,
        keywords: list[str] | None = None,
        tone: str | None = None,
        extra: str | None = None,
        source_url: str | None = None,
    ) -> GeneratedContent:
        """단건 트윗을 생성합니다."""
        user_prompt = build_user_prompt(topic, keywords, tone, extra, source_url)

        console.print("[bold cyan]🔄 최신 정보 검색 + 단건 트윗 생성 중...[/]")
        raw = call_chat(self._config, SYSTEM_PROMPT_SINGLE, user_prompt)
        data = extract_json(raw)

        tweet = GeneratedTweet(
            text=data["text"],
            hashtags=data.get("hashtags", []),
            image_prompt=data.get("image_prompt"),
        )

        content = GeneratedContent(
            post_type=PostType.SINGLE,
            topic=topic,
            keywords=keywords or [],
            tweet=tweet,
            image_prompt=tweet.image_prompt,
            model_used=self._config.chat_model,
            source_url=source_url,
            self_comment=data.get("self_comment"),
        )

        console.print(f"[bold green]✅ 트윗 생성 완료[/] ({len(tweet.text)}자)")
        return content

    def generate_column(
        self,
        research: str,
        *,
        title: str | None = None,
        tone: str | None = None,
        extra: str | None = None,
    ) -> GeneratedContent:
        """리서치 자료(마크다운)를 바탕으로 장문 칼럼을 생성합니다."""
        user_prompt = build_column_user_prompt(research, tone, extra)

        console.print("[bold cyan]🔄 리서치 자료 기반 장문 칼럼 생성 중...[/]")
        raw = call_chat(
            self._config,
            SYSTEM_PROMPT_COLUMN,
            user_prompt,
            temperature=0.7,
            max_output_tokens=8000,
        )
        data = extract_json(raw)

        text = data["text"].strip()
        resolved_title = (
            title
            or data.get("title")
            or (text.splitlines()[0].strip() if text else "칼럼")
        )

        tweet = GeneratedTweet(text=text, image_prompt=data.get("image_prompt"))

        content = GeneratedContent(
            post_type=PostType.COLUMN,
            topic=resolved_title,
            tweet=tweet,
            image_prompt=tweet.image_prompt,
            model_used=self._config.chat_model,
        )

        console.print(f"[bold green]✅ 칼럼 생성 완료[/] ({len(text)}자)")
        return content

    def generate_thread(
        self,
        topic: str,
        keywords: list[str] | None = None,
        tone: str | None = None,
        extra: str | None = None,
        source_url: str | None = None,
    ) -> GeneratedContent:
        """스레드 트윗을 생성합니다."""
        user_prompt = build_user_prompt(topic, keywords, tone, extra, source_url)

        console.print("[bold cyan]🔄 최신 정보 검색 + 스레드 생성 중...[/]")
        raw = call_chat(self._config, SYSTEM_PROMPT_THREAD, user_prompt)
        data = extract_json(raw)

        tweets = [
            GeneratedTweet(
                text=t["text"],
                hashtags=t.get("hashtags", []),
                image_prompt=t.get("image_prompt"),
            )
            for t in data["tweets"]
        ]

        thread = GeneratedThread(topic=data.get("topic", topic), tweets=tweets)
        image_prompt = tweets[0].image_prompt if tweets else None

        content = GeneratedContent(
            post_type=PostType.THREAD,
            topic=topic,
            keywords=keywords or [],
            thread=thread,
            image_prompt=image_prompt,
            model_used=self._config.chat_model,
            source_url=source_url,
            self_comment=data.get("self_comment"),
        )

        console.print(
            f"[bold green]✅ 스레드 생성 완료[/] ({thread.tweet_count}개 트윗)"
        )
        return content
