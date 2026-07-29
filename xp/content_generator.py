"""Grok API를 이용한 X 포스팅 콘텐츠 생성.

X OAuth(구독 로그인) 토큰으로 xAI의 Grok 모델에 접근합니다.
Agent Tools API(/responses)의 web_search·x_search로 최신 사실을 조사한 뒤
콘텐츠를 생성합니다. 토큰 만료(401) 시 강제 갱신 후 재시도합니다.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from rich.console import Console

from xp.config import XAIConfig
from xp.models import (
    GeneratedContent,
    GeneratedThread,
    GeneratedTweet,
    PostType,
)
from xp.prompts import (
    SYSTEM_PROMPT_SINGLE,
    SYSTEM_PROMPT_THREAD,
    build_user_prompt,
)

console = Console()


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 블록을 추출합니다.

    코드 블록(```json ... ```) 안의 JSON이나 순수 JSON을 파싱합니다.
    """
    # 1) ```json ... ``` 블록에서 추출 시도
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())

    # 2) 전체 텍스트를 JSON으로 파싱 시도
    cleaned = text.strip()
    if cleaned.startswith("{") or cleaned.startswith("["):
        return json.loads(cleaned)

    raise ValueError(f"응답에서 유효한 JSON을 찾을 수 없습니다:\n{text[:200]}...")


def _extract_response_text(data: dict) -> str:
    """/responses 응답의 output 배열에서 최종 메시지 텍스트를 뽑아냅니다."""
    parts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for chunk in item.get("content", []):
            if chunk.get("type") in ("output_text", "text"):
                parts.append(chunk.get("text", ""))
    return "".join(parts).strip()


def _post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
    *,
    timeout: int = 120,
    retries: int = 3,
) -> dict:
    """JSON POST 요청. 일시적 오류 시 지수 백오프로 재시도."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt + 1 >= retries:
                raise
            time.sleep(1.5 * (2**attempt))
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise RuntimeError(f"API 요청 실패 ({url}): {exc}") from exc
            time.sleep(1.5 * (2**attempt))
    raise RuntimeError(f"API 요청 실패 ({url}): {last_error}")


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
    ) -> GeneratedContent:
        """단건 트윗을 생성합니다."""
        user_prompt = build_user_prompt(topic, keywords, tone, extra)

        console.print("[bold cyan]🔄 최신 정보 검색 + 단건 트윗 생성 중...[/]")
        raw = self._call_chat(SYSTEM_PROMPT_SINGLE, user_prompt)
        data = _extract_json(raw)

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
        )

        console.print(f"[bold green]✅ 트윗 생성 완료[/] ({len(tweet.text)}자)")
        return content

    def generate_thread(
        self,
        topic: str,
        keywords: list[str] | None = None,
        tone: str | None = None,
        extra: str | None = None,
    ) -> GeneratedContent:
        """스레드 트윗을 생성합니다."""
        user_prompt = build_user_prompt(topic, keywords, tone, extra)

        console.print("[bold cyan]🔄 최신 정보 검색 + 스레드 생성 중...[/]")
        raw = self._call_chat(SYSTEM_PROMPT_THREAD, user_prompt)
        data = _extract_json(raw)

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
        )

        console.print(
            f"[bold green]✅ 스레드 생성 완료[/] ({thread.tweet_count}개 트윗)"
        )
        return content

    # ──────────────────────────────────────────
    # 내부 메서드
    # ──────────────────────────────────────────

    def _call_chat(self, system_prompt: str, user_prompt: str) -> str:
        """Grok Agent Tools API(/responses)를 호출합니다.

        live_search가 켜져 있으면 web_search·x_search 도구를 붙여 최신 사실을
        조사하게 합니다. X OAuth 토큰 만료(401) 시 자동 갱신 후 재시도합니다.
        검색 때문에 응답이 길어질 수 있어 타임아웃을 넉넉히 둡니다.
        """
        key = self._config.get_access_token()

        payload: dict = {
            "model": self._config.chat_model,
            "instructions": system_prompt,
            "input": user_prompt,
            "temperature": 0.8,
        }
        if self._config.live_search:
            payload["tools"] = [{"type": "web_search"}, {"type": "x_search"}]

        url = f"{self._config.base_url}/responses"

        try:
            data = _post_json(url, payload, {"Authorization": f"Bearer {key}"}, timeout=240)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            # OAuth 토큰 만료 -> 강제 갱신 후 재시도
            from xp import grok_oauth
            key = grok_oauth.get_access_token(force_refresh=True)
            data = _post_json(url, payload, {"Authorization": f"Bearer {key}"}, timeout=240)

        content = _extract_response_text(data)
        if not content:
            raise RuntimeError("Grok API가 빈 응답을 반환했습니다.")

        return content
