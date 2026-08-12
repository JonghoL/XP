# XP — X 포스팅 자동화

Grok API(xAI)를 활용하여 X(Twitter) 포스팅용 글과 한국 웹툰 스타일 이미지를 자동
생성하고, 그 이미지를 Grok Image-to-Video로 짧은 영상으로 변환한 뒤, Google Drive에
업로드하고, 최종적으로 X API를 통해 자동 포스팅(영상 첨부)하는 파이프라인입니다.

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
| `XAI_VIDEO_MODEL` | 이미지→영상 변환 모델 (기본: `grok-imagine-video`) |
| `GOOGLE_SA_KEY_PATH` | Google Service Account JSON 경로 |
| `GDRIVE_FOLDER_ID` | Google Drive 대상 폴더 ID |

## 사용법

### 글 + 이미지 + 영상 생성

기본적으로 이미지 생성 후, 그 이미지를 입력으로 Grok Image-to-Video를 호출해
짧은 영상(`post_video.mp4`)까지 만듭니다. `--post`로 바로 게시하면 이미지 대신
이 영상이 첨부됩니다.

```powershell
# 단건 트윗 (이미지 → 영상까지 자동 생성)
python -m xp generate --topic "AI 트렌드" --type single

# 스레드 트윗
python -m xp generate --topic "2026 AI 트렌드 총정리" --type thread --keywords "LLM,AGI,멀티모달"

# 이미지 없이 글만
python -m xp generate --topic "AI 트렌드" --no-image

# 이미지는 만들되 영상 변환은 생략
python -m xp generate --topic "AI 트렌드" --no-video

# 글 + 이미지/영상 + Google Drive 업로드
python -m xp generate --topic "AI 트렌드" --type single --upload

# 생성부터 X 게시까지 원스텝 (--post, 영상이 있으면 영상으로 게시)
python -m xp generate --topic "AI 트렌드" --type single --post
```

### 장문 칼럼 (리서치 md 기반 롱폼)

주제만 던지는 짧은 트윗과 달리, **내가 직접 리서치한 자료(마크다운)를 근간으로
한 편의 완결된 장문 칼럼(롱폼)**을 작성하는 기능입니다. 최신 이슈를 깊이 있게
다루거나, 내가 수집한 팩트·관점을 그대로 살리고 싶을 때 사용합니다.

**동작 방식**

1. 입력 폴더(`input/`, 환경변수 `XP_INPUT_DIR`로 변경 가능)에 리서치 `.md` 파일을 넣습니다.
2. `python -m xp column`을 실행하면 Grok이 그 자료를 근간으로 헤드라인 + 문단 구성을
   갖춘 칼럼을 작성합니다(도입 → 전개 → 통찰 → 마무리, 대략 1,200~3,000자).
3. 결과가 `output/<날짜>-<헤드라인>/`에 저장되고, 처리한 원본 md는 `input/processed/`로
   이동해 다음 실행 때 중복 처리되지 않습니다.

**리서치 md 작성 팁** — 형식은 자유이며, 아래처럼 팩트와 내 관점을 적어두면
그대로 칼럼에 반영됩니다.

```markdown
# 주제 메모

## 핵심 팩트
- 수치/사건/발표 (예: 7월 순매수 5.2조원, 전월 대비 5배)
- 배경과 원인
- 리스크

## 내 관점
- 강조하고 싶은 논지나 결론
```

**명령어**

```powershell
# input/ 의 모든 .md 를 칼럼으로 작성 (처리 후 processed/ 로 이동)
python -m xp column

# 특정 파일만 지정 (이 경우 원본을 이동하지 않음)
python -m xp column --file "research/서학개미.md"

# 헤더 웹툰 이미지 생략
python -m xp column --no-image

# 이미지는 만들되 영상 변환은 생략
python -m xp column --no-video

# 헤드라인을 직접 지정 (생략 시 모델이 생성)
python -m xp column --title "내가 정한 제목"

# 작성 직후 바로 X에 게시
python -m xp column --post --method auto

# 처리 후 원본 md 를 processed/ 로 옮기지 않고 그대로 두기
python -m xp column --keep
```

**옵션 요약**

| 옵션 | 설명 |
|------|------|
| `--file`, `-f` | 처리할 md 파일 (생략 시 `input/`의 모든 `.md`) |
| `--title` | 헤드라인 강제 지정 (생략 시 모델 생성) |
| `--tone` | 글의 톤 (예: 분석적, 논쟁적) |
| `--extra` | 추가 지시 사항 |
| `--no-image` | 헤더 이미지 생략 |
| `--no-video` | 이미지 → 영상 변환 생략 (이미지만 사용) |
| `--upload`, `-u` | Google Drive 업로드 |
| `--post`, `-p` | 작성 직후 X에 게시 |
| `--method` | 게시 방식 `api`(기본)/`browser`/`auto` |
| `--keep` | 처리 후 원본 md를 이동하지 않음 |

**결과물** — 프로젝트 폴더에 다음이 저장됩니다.

```text
output/2026-07-30-국장을-떠난-5조-원.../
  column.txt        ← 칼럼 전문 (헤드라인 + 본문)
  meta.json         ← 메타데이터 (post_type: column 등)
  post_image.png    ← 헤더 웹툰 이미지 (--no-image 아니면)
  post_video.mp4    ← 헤더 이미지를 변환한 영상 (--no-video 아니면)
```

> ⚠️ **X Premium 필요** — 280자를 넘는 롱폼을 실제로 게시하려면 게시 계정이
> X Premium이어야 합니다. 그렇지 않으면 API가 길이 초과로 거부합니다.
> (작성·저장 자체는 계정과 무관하게 됩니다.)

#### X 아티클(배너형 롱폼)로 발행하기

상단에 배너(커버) 이미지가 박히는 **X 아티클**은 공개 API가 없어 자동 게시가
불가능합니다(웹 아티클 작성기에서 직접 발행, X Premium+ 필요). 대신 `article`
명령이 붙여넣기 좋게 준비해 줍니다 — 제목/본문을 분리하고 **본문을 클립보드에
자동 복사**합니다.

```powershell
python -m xp article --dir "output/2026-07-30-..."
```

그다음 X 웹의 **‘아티클 작성(Write Article)’**에서
① 커버에 `post_image.png` 업로드 → ② 제목 붙여넣기 → ③ 본문 붙여넣기(클립보드에 있음)
→ ④ 게시. (일반 롱폼 포스트로 올릴 거면 이 단계 없이 `post`/`--post`를 쓰면 됩니다.)

**브라우저 완전 자동 발행 (`--post`)** — 작성기를 열어 제목·커버·본문을 채우고
게시까지 **수동 개입 없이** 자동 진행합니다. 최초 1회 `python -m xp x-browser-login`
으로 로그인해 두어야 합니다(X Premium+ 필요).

```powershell
# input/ md → 칼럼 생성 → 아티클 작성기 자동 조작 → 발행까지
python -m xp article --post

# 기존 칼럼 폴더로
python -m xp article --dir "output/..." --post

# 발행 직전까지만 채우고, 브라우저에서 검토 후 직접 게시
python -m xp article --dir "output/..." --post --review
```

> ⚠️ 아티클은 공개 API가 없어 웹 UI를 자동 조작합니다. DOM(셀렉터)이 실측
> 검증되지 않아, 필수 요소(제목/본문/게시 버튼)를 못 찾으면 **페이지 덤프
> (HTML·스크린샷)를 프로젝트 폴더에 저장하고 명확히 실패**합니다. 그 덤프로
> `xp/x_poster_browser.py` 상단 `SEL_ART_*` 상수를 실제 DOM에 맞게 고치면
> 이후엔 완전 자동으로 돌아갑니다.

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

`--type random`을 주면 단건 60% : 스레드 30% 비중(2:1 가중치)으로 랜덤 선택합니다.
`--avoid-recent`(기본 20)로 최근 몇 개 주제까지 중복을 피할지 조정합니다.

#### 니치 로테이션 (콘텐츠 축 고정)

`--category`를 직접 주지 않으면, `xp/pillars.py`에 정의된 3개 니치 축 중
하나를 실행마다 가중치 기반으로 자동 선택합니다. 계정 정체성을 흩뜨리지
않으면서도 매번 같은 주제만 반복되지 않도록 하기 위함입니다.

| 축 | 이름 | 상대 비중 |
|----|------|-----------|
| A | AI·자동화 부수입 | 4 |
| B | 코드·개발 생산성 | 3 |
| C | 시장·투자 인사이트 | 1.5 |

```powershell
# 특정 축으로 미리보기
python -m xp topics --pillar A --count 5

# 특정 축을 강제 지정해 생성 (기본은 미지정 -> 자동 로테이션)
python -m xp auto --pillar B
```

`--category`를 명시하면 니치 로테이션 대신 그 값을 그대로 사용합니다.
선택된 축과 주제는 `meta.json`의 `pillar` 필드와 `xp-posts.log`에 함께 기록됩니다.

**셀프 댓글(출처)** — `topics`/`auto`는 Grok이 web_search·x_search로 주제를
조사하면서 근거가 된 출처 URL(`source_url`)도 함께 받아옵니다. 단건/스레드
게시(`--post`) 시 출처가 있으면, 본문 게시 직후 그 링크를 소개하는 짧은
코멘트(Grok이 1~2문장 생성)와 함께 **자신의 게시물에 셀프 답글**을 자동으로
답니다(트윗 본문에는 URL을 넣지 않는 기존 원칙 유지). 칼럼/아티클에는 적용되지
않습니다.

### 스케줄링 (cron / 작업 스케줄러)

XP는 상주 데몬 없이, OS의 스케줄러(cron, Windows 작업 스케줄러)가
`xp auto`를 주기적으로 실행하는 방식을 씁니다. `schedule` 명령은 등록할
명령어를 안내만 하며, 실제 등록(crontab 편집 등)은 직접 수행해야 합니다.

**하루 여러 회 고정 슬롯 (권장)** — 매시간 자동 발행은 스팸 신호로 읽히기
쉬우므로, 하루 1~2회 정도의 고정 시간대로 발행량을 낮추는 쪽을 권장합니다.

```powershell
python -m xp schedule --times "07:30,20:00" --upload --post --method browser
```

> 💡 **브라우저 우선 · 유료 API 최소화** — 스케줄 게시는 `--method browser`를
> 권장합니다(기본값). 이 경로는 다음을 보장합니다.
>
> - **세션 쿠키 파일 영속**: 로그인 시 쿠키를 `~/.xp/x_browser/xp_session.json`으로
>   저장하고 게시 직전 다시 주입합니다. 크론(GUI 세션 없는 launchd)에서 Chromium이
>   프로필 쿠키를 flush하지 못해 세션이 통째로 사라지던 문제를 회피합니다.
> - **헤드리스 기본**: 스케줄 실행은 창을 띄우지 않습니다(`XP_BROWSER_HEADLESS=0`으로 끌 수 있음).
> - **게시 전 로그인 검증**: 세션이 만료됐으면 '작성창 못 찾음' 같은 모호한 실패
>   대신 즉시 구분해 알리고, **유료 API로 폴백하지 않습니다.**
> - **재시도 큐**: 일시적 실패는 백오프 3회 재시도하고, 그래도 실패하면
>   `post-queue.jsonl`에 넣어 **다음 스케줄 슬롯에서 브라우저로 다시 시도**합니다
>   (`xp auto`는 매 실행 시작에 큐를 먼저 비웁니다). 수동 재시도는 `python -m xp retry`.
> - **세션 만료 알림**: 재로그인이 필요하면 macOS 데스크톱 알림 + 로그로 통지합니다.
>   `python -m xp x-browser-login`으로 다시 로그인하면 큐가 다음 실행에 자동 처리됩니다.
>
> `--method auto`는 브라우저 실패 시 **유료 API로 폴백**하므로, 비용을 피하려면
> `browser`를 쓰세요.

**하루 한 번 고정 시각**

```powershell
python -m xp schedule --hour 9 --minute 0 --upload --post --method auto
```

**매시간 + 랜덤 지연** — 여전히 필요하다면(예: 실험적 고빈도 운영) 실행
시각을 매번 무작위로 흔드는(정각 이후 0~N분 랜덤 지연) 방식도 남아 있습니다.

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
    tweet.txt            ← 트윗 본문 (스레드는 thread.txt, 칼럼은 column.txt)
    meta.json            ← 메타데이터 (주제, 유형, 모델 등)
    post_image.png       ← 생성된 이미지 (한국 웹툰 스타일)
    post_video.mp4       ← 위 이미지를 Grok Image-to-Video로 변환한 영상
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
디렉토리에 `post_video.mp4`가 있으면 영상을, 없으면 이미지를 첫 트윗에
첨부합니다 (영상 업로드는 X API v1.1 chunked upload를 사용합니다).

```powershell
# 이미지/영상 포함 게시 (영상이 있으면 영상 우선)
python -m xp post --dir "output/2026-07-29-ai-트렌드"

# 텍스트만 게시
python -m xp post --dir "output/2026-07-29-ai-트렌드" --no-image
```

> ℹ️ **영상 생성 API는 비공식** — `POST /v1/videos/generations`와
> `GET /v1/videos/{request_id}`는 xAI 공식 문서(docs.x.ai)에는 없지만 실제
> 호출로 동작을 확인해 구현했습니다(2026-08-08 기준, `xp/video_generator.py`
> 상단 주석 참고). 비공식 엔드포인트라 추후 xAI 쪽 변경으로 깨질 수 있습니다.

### 브라우저 게시

DrissionPage로 실제 브라우저를 구동해 x.com에 게시하는 경로입니다.

```powershell
pip install -e ".[browser]"
```

전용 브라우저 프로필에 **최초 1회 수동 로그인**해 세션을 만듭니다
(비밀번호를 저장하지 않고 세션 쿠키만 재사용). 로그인은 성공을 실제로 검증한
뒤에만 쿠키를 `~/.xp/x_browser/xp_session.json`으로 저장합니다:

```powershell
python -m xp x-browser-login
```

이후 `--method`로 게시 방식을 고릅니다:

```powershell
# 브라우저만 사용 (유료 API 폴백 없음, 스케줄 권장) — 실패 시 재시도 큐로
python -m xp post --dir "output/..." --method browser

# 브라우저 먼저, 실패 시 유료 API로 폴백
python -m xp post --dir "output/..." --method auto

# API 강제 (유료)
python -m xp post --dir "output/..." --method api
```

게시 방식별 동작:

| method | 동작 | 유료 API |
|--------|------|:-------:|
| `browser` | 헤드리스 게시. 일시 실패는 3회 재시도, 세션 만료면 즉시 중단·알림 | ❌ 안 씀 |
| `auto` | 브라우저 먼저, 실패하면 API로 폴백 | ⚠️ 실패 시 사용 |
| `api` | X API로만 게시 | ✅ 항상 |

> ⚠️ 웹 UI 자동화는 X의 DOM 변경·봇 탐지에 취약하고 X 이용약관상 권장되지
> 않습니다. `XP_BROWSER_PROFILE`(프로필 경로), `XP_BROWSER_HEADLESS=0`(창 표시)로
> 조정합니다(기본은 헤드리스). 크론에서 이 방식을 쓰려면, 실행에 사용하는
> 파이썬 인터프리터에 브라우저 의존성(`pip install -e ".[browser]"`)이 설치돼
> 있어야 합니다.
>
> **세션이 만료되면** `browser`는 유료 API로 넘어가지 않고 해당 건을
> `post-queue.jsonl`에 넣은 뒤 재로그인을 알립니다. `python -m xp x-browser-login`
> 으로 다시 로그인하면, 다음 `xp auto` 실행(또는 `python -m xp retry`)이 큐를
> 자동으로 비워 브라우저로 재게시합니다.
