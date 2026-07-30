# XP — X 포스팅 자동화

Grok API(xAI)를 활용하여 X(Twitter) 포스팅용 글과 이미지를 자동 생성하고,
Google Drive에 업로드한 뒤, 최종적으로 X API를 통해 자동 포스팅하는 파이프라인입니다.

## 요구 사항

- Python 3.11+
- SuperGrok 또는 X Premium+ 구독 계정 (X OAuth 로그인용)

## 설치

```powershell
cd XP
pip install -e .
```

## 설정

### 1) X OAuth 로그인 (Grok 인증)

Grok 인증은 API 키가 아니라 **X OAuth(구독 로그인)**으로만 수행합니다.
아래 명령을 실행하면 브라우저에서 X 계정으로 로그인을 승인하고,
토큰이 `~/.naverblog/grok_oauth.json` 에 저장됩니다. (최초 1회)

```powershell
python -m xp grok-login
```

토큰은 만료 시 자동으로 갱신되므로 재로그인은 거의 필요하지 않습니다.

### 2) 환경변수 (선택)

`.env.example`을 복사하여 `.env`로 만든 뒤 필요한 값만 입력하세요.

```powershell
copy .env.example .env
```

| 변수 | 설명 |
|------|------|
| `XAI_CHAT_MODEL` | 텍스트 생성 모델 (기본: `grok-4.5`) |
| `XAI_IMAGE_MODEL` | 이미지 생성 모델 (기본: `grok-imagine-image`) |
| `GOOGLE_SA_KEY_PATH` | Google Service Account JSON 경로 |
| `GDRIVE_FOLDER_ID` | Google Drive 대상 폴더 ID |

## 사용법

### 글 + 이미지 생성

```powershell
# 단건 트윗
python -m xp generate --topic "AI 트렌드" --type single

# 스레드 트윗
python -m xp generate --topic "2026 AI 트렌드 총정리" --type thread --keywords "LLM,AGI,멀티모달"

# 이미지 없이 글만
python -m xp generate --topic "AI 트렌드" --no-image

# 글 + 이미지 + Google Drive 업로드
python -m xp generate --topic "AI 트렌드" --type single --upload

# 생성부터 X 게시까지 원스텝 (--post)
python -m xp generate --topic "AI 트렌드" --type single --post
```

> `--post`는 생성 직후 바로 X에 게시합니다. Phase 2 설정(`pip install -e ".[phase2]"` +
> X API 키 4개)이 되어 있어야 하며, 실제 공개 게시이므로 신중히 사용하세요.

### 주제 자동 탐색 + 자동 포스팅

Grok에게 지금 X에서 화제가 될 만한 주제를 조사·제안받아 자동으로 글을 생성합니다.
최근 생성된 주제와는 겹치지 않도록 제외합니다.

```powershell
# 화제 주제 5개 미리 보기 (생성은 하지 않음)
python -m xp topics --count 5

# 주제 1개를 자동 선정해 생성까지 (사람 개입 없이 실행 가능, 스케줄러용)
python -m xp auto

# 생성 + 업로드 + 게시까지 원스텝
python -m xp auto --upload --post --method auto
```

`--type random`을 주면 단건/스레드를 무작위로 선택합니다.
`--category`로 분야를 좁힐 수 있고, `--avoid-recent`(기본 20)로 최근 몇 개
주제까지 중복을 피할지 조정합니다.

### 스케줄링 (cron / 작업 스케줄러)

XP는 상주 데몬 없이, OS의 스케줄러(cron, Windows 작업 스케줄러)가
`xp auto`를 주기적으로 실행하는 방식을 씁니다. `schedule` 명령은 등록할
명령어를 안내만 하며, 실제 등록(crontab 편집 등)은 직접 수행해야 합니다.

```powershell
python -m xp schedule --hour 9 --minute 0 --upload --post --method auto
```

매일 고정 시각 대신 매시간 실행하면서, 실행 시각을 매번 무작위로 흔들어
(정각 이후 0~N분 랜덤 지연) 패턴 탐지를 피하고 싶다면 `--every-hour`를 사용하세요.

```powershell
python -m xp schedule --every-hour --jitter-minutes 50 --upload --post --method auto
```

cron은 매시 정각에 트리거되지만, 실제 `xp auto` 실행은 `sleep`으로 0~50분
사이 무작위 지연된 뒤 이루어져 매번 다른 시각에 게시됩니다.

### Google Drive 업로드

```powershell
python -m xp upload --dir "output/2026-07-29-ai-트렌드"
```

### 히스토리 보기

```powershell
python -m xp list
```

## 생성 구조

```text
output/
  2026-07-29-ai-트렌드/
    tweet.txt            ← 트윗 본문
    meta.json            ← 메타데이터 (주제, 키워드, 모델 등)
    post_image.png       ← 생성된 이미지
```

## Phase 2: X 자동 포스팅

트윗 게시는 Grok용 X OAuth(구독 로그인)와 **별개**로, X 계정의
OAuth 1.0a 사용자 토큰(consumer/access key)을 사용합니다.
(트윗 생성은 X API v2, 이미지 업로드는 v1.1을 사용)

먼저 tweepy를 설치합니다:

```powershell
pip install -e ".[phase2]"
```

[X Developer Portal](https://developer.x.com)에서 앱을 만들고(권한: Read and write),
아래 4개 값을 `.env`에 추가합니다:

```dotenv
X_CONSUMER_KEY=...
X_CONSUMER_SECRET=...
X_ACCESS_TOKEN=...
X_ACCESS_TOKEN_SECRET=...
```

그런 다음 생성된 프로젝트 디렉토리를 게시합니다. 단건/스레드는
`meta.json`으로 자동 판별하며, 스레드는 답글 체인으로 연결됩니다.
디렉토리 내 이미지는 첫 트윗에 첨부됩니다.

```powershell
# 이미지 포함 게시
python -m xp post --dir "output/2026-07-29-ai-트렌드"

# 텍스트만 게시
python -m xp post --dir "output/2026-07-29-ai-트렌드" --no-image
```

### 비상용 브라우저 게시 (장애 대응)

X API가 막히거나(레이트리밋·쿼터 소진·장애) 사용할 수 없을 때를 대비한
폴백 경로입니다. DrissionPage로 실제 브라우저를 구동해 x.com에 게시합니다.

```powershell
pip install -e ".[browser]"
```

전용 브라우저 프로필에 **최초 1회 수동 로그인**해 세션을 만듭니다
(비밀번호를 저장하지 않고 세션 쿠키만 재사용):

```powershell
python -m xp x-browser-login
```

이후 `--method`로 게시 방식을 고릅니다:

```powershell
# API 실패 시 자동으로 브라우저로 폴백 (권장)
python -m xp post --dir "output/..." --method auto

# 브라우저 강제 (비상)
python -m xp post --dir "output/..." --method browser
```

> ⚠️ 웹 UI 자동화는 X의 DOM 변경·봇 탐지에 취약하고 X 이용약관상 권장되지
> 않습니다. **정상 경로는 API이며, 이것은 장애 시 임시 대응용**입니다.
> `XP_BROWSER_PROFILE`(프로필 경로), `XP_BROWSER_HEADLESS=1`(헤드리스)로 조정합니다.
