"""XP 데이터 모델."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class PostType(str, Enum):
    """포스팅 유형."""

    SINGLE = "single"
    THREAD = "thread"
    COLUMN = "column"  # 리서치 md 기반 장문 칼럼 (롱폼)


class GeneratedTweet(BaseModel):
    """생성된 단건 트윗."""

    text: str = Field(..., description="트윗 본문 (280자 이내)")
    hashtags: list[str] = Field(default_factory=list, description="해시태그 목록")
    image_prompt: str | None = Field(None, description="이미지 생성용 프롬프트")

    @property
    def full_text(self) -> str:
        """해시태그를 포함한 전체 텍스트."""
        if not self.hashtags:
            return self.text
        # 모델이 '#'를 붙여 반환하기도 하므로 중복을 방지한다.
        tags = " ".join(f"#{t.lstrip('#')}" for t in self.hashtags)
        return f"{self.text}\n\n{tags}"


class GeneratedThread(BaseModel):
    """생성된 스레드 트윗."""

    tweets: list[GeneratedTweet] = Field(..., description="스레드를 구성하는 트윗 목록")
    topic: str = Field(..., description="스레드 주제")

    @property
    def tweet_count(self) -> int:
        return len(self.tweets)


class SuggestedTopic(BaseModel):
    """Grok이 제안한 포스팅 주제."""

    topic: str = Field(..., description="제안된 주제")
    keywords: list[str] = Field(default_factory=list, description="관련 키워드")
    reason: str | None = Field(None, description="이 주제를 추천하는 이유")


class GeneratedContent(BaseModel):
    """콘텐츠 생성 결과."""

    post_type: PostType
    topic: str
    keywords: list[str] = Field(default_factory=list)
    tweet: GeneratedTweet | None = None
    thread: GeneratedThread | None = None
    image_prompt: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    model_used: str = ""


class GeneratedImage(BaseModel):
    """생성된 이미지 메타데이터."""

    prompt: str
    local_path: Path
    model_used: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class GeneratedVideo(BaseModel):
    """Grok Image-to-Video로 생성된 영상 메타데이터."""

    prompt: str
    local_path: Path
    source_image: Path | None = None
    model_used: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class UploadResult(BaseModel):
    """Google Drive 업로드 결과."""

    file_id: str
    file_name: str
    web_view_link: str | None = None
    web_content_link: str | None = None


class PostResult(BaseModel):
    """X 포스팅 결과 — Phase 2."""

    tweet_id: str
    text: str
    url: str
    media_ids: list[str] = Field(default_factory=list)
    posted_at: datetime = Field(default_factory=datetime.now)


class ProjectOutput(BaseModel):
    """한 번의 실행으로 생성된 전체 결과."""

    project_dir: Path
    content: GeneratedContent
    images: list[GeneratedImage] = Field(default_factory=list)
    videos: list[GeneratedVideo] = Field(default_factory=list)
    uploads: list[UploadResult] = Field(default_factory=list)
    posts: list[PostResult] = Field(default_factory=list)
