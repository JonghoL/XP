"""xAI Grok Agent Tools API(/responses) 호출 공통 로직.

ContentGenerator와 TopicFinder가 공유하는 HTTP/재시도/OAuth 갱신 로직을 모읍니다.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from xp.config import XAIConfig


def extract_json(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 블록을 추출합니다.

    코드 블록(```json ... ```) 안의 JSON이나 순수 JSON을 파싱합니다.
    """
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())

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


def call_chat(
    config: XAIConfig,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.8,
    use_search_tools: bool | None = None,
    timeout: int = 240,
) -> str:
    """Grok Agent Tools API(/responses)를 호출하고 응답 텍스트를 반환합니다.

    live_search가 켜져 있으면 web_search·x_search 도구를 붙여 최신 사실을
    조사하게 합니다. X OAuth 토큰 만료(401) 시 자동 갱신 후 재시도합니다.
    """
    key = config.get_access_token()

    payload: dict = {
        "model": config.chat_model,
        "instructions": system_prompt,
        "input": user_prompt,
        "temperature": temperature,
    }
    search = config.live_search if use_search_tools is None else use_search_tools
    if search:
        payload["tools"] = [{"type": "web_search"}, {"type": "x_search"}]

    url = f"{config.base_url}/responses"

    try:
        data = _post_json(url, payload, {"Authorization": f"Bearer {key}"}, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        from xp import grok_oauth

        key = grok_oauth.get_access_token(force_refresh=True)
        data = _post_json(url, payload, {"Authorization": f"Bearer {key}"}, timeout=timeout)

    content = _extract_response_text(data)
    if not content:
        raise RuntimeError("Grok API가 빈 응답을 반환했습니다.")

    return content
