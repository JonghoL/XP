"""X(Twitter) 자동 포스팅 — Phase 2.

tweepy로 X API에 트윗을 게시합니다.
- 트윗 생성: X API v2 (create_tweet)
- 이미지 업로드: X API v1.1 media_upload (OAuth 1.0a 필요)

인증은 X 계정의 OAuth 1.0a 사용자 토큰(consumer key/secret + access token/secret)을
사용합니다. Grok용 X OAuth(구독 로그인)와는 별개의 자격증명입니다.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from xp.config import XAPIConfig
from xp.models import GeneratedContent, PostResult, PostType

console = Console()

# X는 트윗당 이미지 최대 4장까지 첨부 가능합니다.
MAX_MEDIA_PER_TWEET = 4


class XPoster:
    """X API로 트윗/스레드를 게시합니다."""

    def __init__(self, config: XAPIConfig) -> None:
        self._config = config
        self._v2 = None
        self._v1 = None

    # ──────────────────────────────────────────
    # 공개 메서드
    # ──────────────────────────────────────────

    def post_content(
        self,
        content: GeneratedContent,
        image_paths: list[Path] | None = None,
    ) -> list[PostResult]:
        """콘텐츠 유형에 맞춰 트윗 또는 스레드를 게시합니다."""
        images = image_paths or []

        if content.post_type in (PostType.SINGLE, PostType.COLUMN):
            if content.tweet is None:
                raise ValueError("단건/칼럼 콘텐츠에 본문이 없습니다.")
            result = self._post_single(content.tweet.full_text, images)
            return [result]

        if content.thread is None:
            raise ValueError("스레드 콘텐츠에 thread가 없습니다.")
        return self._post_thread(
            [t.full_text for t in content.thread.tweets], images
        )

    # ──────────────────────────────────────────
    # 내부 메서드
    # ──────────────────────────────────────────

    def _post_single(self, text: str, image_paths: list[Path]) -> PostResult:
        """단건 트윗을 게시합니다."""
        console.print("[bold cyan]🐦 트윗 게시 중...[/]")
        media_ids = self._upload_media(image_paths[:MAX_MEDIA_PER_TWEET])
        result = self._create_tweet(text, media_ids=media_ids)
        console.print(f"[bold green]✅ 게시 완료:[/] {result.url}")
        return result

    def _post_thread(
        self, texts: list[str], image_paths: list[Path]
    ) -> list[PostResult]:
        """스레드를 순차 게시합니다 (첫 트윗에만 이미지 첨부)."""
        console.print(f"[bold cyan]🧵 스레드 게시 중... ({len(texts)}개)[/]")
        results: list[PostResult] = []
        reply_to: str | None = None

        for i, text in enumerate(texts):
            media_ids = (
                self._upload_media(image_paths[:MAX_MEDIA_PER_TWEET])
                if i == 0
                else []
            )
            result = self._create_tweet(
                text, media_ids=media_ids, in_reply_to=reply_to
            )
            reply_to = result.tweet_id
            results.append(result)
            console.print(
                f"[bold green]✅ {i + 1}/{len(texts)} 게시:[/] {result.url}"
            )

        return results

    def _create_tweet(
        self,
        text: str,
        *,
        media_ids: list[str] | None = None,
        in_reply_to: str | None = None,
    ) -> PostResult:
        """트윗 하나를 생성하고 PostResult를 반환합니다."""
        kwargs: dict = {"text": text}
        if media_ids:
            kwargs["media_ids"] = media_ids
        if in_reply_to:
            kwargs["in_reply_to_tweet_id"] = in_reply_to

        response = self._client_v2().create_tweet(**kwargs)
        tweet_id = str(response.data["id"])

        return PostResult(
            tweet_id=tweet_id,
            text=text,
            url=f"https://x.com/i/web/status/{tweet_id}",
            media_ids=list(media_ids or []),
        )

    def _upload_media(self, image_paths: list[Path]) -> list[str]:
        """이미지를 업로드하고 media_id 리스트를 반환합니다."""
        media_ids: list[str] = []
        for path in image_paths:
            if not path.exists():
                console.print(f"[yellow]⚠️  이미지 없음, 건너뜀: {path}[/]")
                continue
            media = self._api_v1().media_upload(filename=str(path))
            media_ids.append(str(media.media_id))
            console.print(f"   🖼️  이미지 업로드: {path.name}")
        return media_ids

    # ── tweepy 클라이언트 (지연 초기화) ──

    def _client_v2(self):  # noqa: ANN202
        """X API v2 클라이언트 (트윗 생성용)."""
        if self._v2 is None:
            import tweepy

            self._v2 = tweepy.Client(
                consumer_key=self._config.consumer_key,
                consumer_secret=self._config.consumer_secret,
                access_token=self._config.access_token,
                access_token_secret=self._config.access_token_secret,
            )
        return self._v2

    def _api_v1(self):  # noqa: ANN202
        """X API v1.1 클라이언트 (미디어 업로드용, OAuth 1.0a)."""
        if self._v1 is None:
            import tweepy

            auth = tweepy.OAuth1UserHandler(
                self._config.consumer_key,
                self._config.consumer_secret,
                self._config.access_token,
                self._config.access_token_secret,
            )
            self._v1 = tweepy.API(auth)
        return self._v1
