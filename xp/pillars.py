"""콘텐츠 니치 로테이션 — 3개 고정 축 (돈버는코드 콘텐츠 전략).

`xp auto`가 매 실행마다 이 중 하나를 가중치 기반으로 골라 그 안에서 주제를
찾습니다. 완전 무작위 주제 탐색 대신 이 축으로 고정해야, 팔로워와 알고리즘
양쪽이 계정 정체성을 학습할 수 있습니다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Pillar:
    key: str
    name: str
    category: str  # TopicFinder에 넘길 category 문자열
    weight: float  # 선정 확률에 비례하는 상대 가중치


PILLARS: list[Pillar] = [
    Pillar(
        key="A",
        name="AI·자동화 부수입",
        category="AI 도구·자동화로 실제 수익을 내는 사례 — 재현 가능한 방법과 구체적 수치",
        weight=4,
    ),
    Pillar(
        key="B",
        name="코드·개발 생산성",
        category="개발자가 바로 써먹는 코드 스니펫·자동화 워크플로·생산성 팁",
        weight=3,
    ),
    Pillar(
        key="C",
        name="시장·투자 인사이트",
        category="AI·반도체·해외주식 등 시장 이슈를 개발자/엔지니어 관점에서 해석",
        weight=1.5,
    ),
]


def choose_pillar() -> Pillar:
    """가중치 기반으로 니치 축 하나를 무작위 선택합니다."""
    return random.choices(PILLARS, weights=[p.weight for p in PILLARS], k=1)[0]


def get_pillar(key: str) -> Pillar:
    """key(A/B/C, 대소문자 무관) 또는 이름으로 pillar를 찾습니다."""
    key_norm = key.strip().lower()
    for p in PILLARS:
        if p.key.lower() == key_norm or p.name == key:
            return p
    valid = ", ".join(p.key for p in PILLARS)
    raise ValueError(f"알 수 없는 pillar: {key!r} (사용 가능: {valid})")
