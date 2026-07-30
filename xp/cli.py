"""XP CLI — X 포스팅 자동화 명령줄 인터페이스.

명령어:
    generate  글 + 이미지 생성 (--upload 으로 GDrive 업로드)
    topics    Grok에게 최신 화제 기반 주제 제안받기
    auto      주제 자동 선정 -> 생성 -> (선택)업로드/게시, cron 등 스케줄러용
    schedule  OS 스케줄러(cron/작업 스케줄러) 등록 안내 출력
    upload    이미 생성된 파일을 GDrive에 업로드
    post      생성된 콘텐츠를 X에 게시
    list      생성 히스토리 보기
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from xp.config import AppConfig, load_config
from xp.content_generator import ContentGenerator
from xp.image_generator import ImageGenerator
from xp.models import GeneratedContent, PostResult, PostType, ProjectOutput

console = Console()

POST_LOG_PATH = Path("xp-posts.log")


def _log_post_result(
    topic: str, posts: list[PostResult] | None, error: str | None = None
) -> None:
    """게시 결과(성공/실패)를 xp-posts.log에 한 줄씩 기록합니다."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    if error is not None:
        lines.append(f"[{ts}] FAILED topic={topic!r} error={error!r}")
    elif posts:
        for p in posts:
            lines.append(f"[{ts}] POSTED topic={topic!r} url={p.url} text={p.text!r}")
    else:
        lines.append(f"[{ts}] SKIPPED topic={topic!r} (게시 안 함)")

    with POST_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ──────────────────────────────────────────────
# 프로젝트 디렉토리 관리
# ──────────────────────────────────────────────


def _make_project_dir(output_dir: Path, topic: str) -> Path:
    """날짜 + 주제 기반 프로젝트 디렉토리를 생성합니다."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    # 주제에서 안전한 폴더명 생성
    safe_topic = "".join(
        c if c.isalnum() or c in ("-", "_") else "-" for c in topic
    )
    safe_topic = safe_topic[:40].strip("-")

    base_name = f"{date_str}-{safe_topic}"
    project_dir = output_dir / base_name

    # 중복 시 숫자 접미사
    counter = 2
    while project_dir.exists():
        project_dir = output_dir / f"{base_name}-{counter}"
        counter += 1

    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def _recent_topics(output_dir: Path, limit: int) -> list[str]:
    """최근 생성된 프로젝트들의 주제를 반환합니다 (중복 주제 회피용)."""
    if not output_dir.exists() or limit <= 0:
        return []

    dirs = sorted(
        (d for d in output_dir.iterdir() if d.is_dir()), reverse=True
    )[:limit]

    topics: list[str] = []
    for d in dirs:
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        topic = meta.get("topic")
        if topic:
            topics.append(topic)
    return topics


def _save_content_files(project_dir: Path, output: ProjectOutput) -> None:
    """생성 결과를 파일로 저장합니다."""
    content = output.content

    # 트윗/칼럼 텍스트 저장
    if content.tweet:
        fname = "column.txt" if content.post_type == PostType.COLUMN else "tweet.txt"
        (project_dir / fname).write_text(content.tweet.full_text, encoding="utf-8")

    if content.thread:
        thread_path = project_dir / "thread.txt"
        lines: list[str] = []
        for i, t in enumerate(content.thread.tweets, 1):
            lines.append(f"── 트윗 {i}/{content.thread.tweet_count} ──")
            lines.append(t.full_text)
            lines.append("")
        thread_path.write_text("\n".join(lines), encoding="utf-8")

    # 메타데이터 저장
    meta_path = project_dir / "meta.json"
    meta = content.model_dump(mode="json")
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────
# generate 명령
# ──────────────────────────────────────────────


def _generate_pipeline(
    config: AppConfig,
    *,
    topic: str,
    post_type: PostType,
    keywords: list[str] | None,
    tone: str | None,
    extra: str | None,
    no_image: bool,
    upload: bool,
    post: bool,
    method: str,
) -> ProjectOutput:
    """주제 하나에 대해 생성 -> (선택)업로드/게시까지 수행합니다.

    `generate`/`auto` 명령이 공유하는 핵심 파이프라인입니다.
    """
    # 1) 콘텐츠 생성
    gen = ContentGenerator(config.xai)

    if post_type == PostType.SINGLE:
        content = gen.generate_single(
            topic=topic, keywords=keywords, tone=tone, extra=extra
        )
    else:
        content = gen.generate_thread(
            topic=topic, keywords=keywords, tone=tone, extra=extra
        )

    return _finalize_output(
        config,
        content,
        no_image=no_image,
        upload=upload,
        post=post,
        method=method,
    )


def _finalize_output(
    config: AppConfig,
    content: GeneratedContent,
    *,
    no_image: bool,
    upload: bool,
    post: bool,
    method: str,
) -> ProjectOutput:
    """생성된 콘텐츠에 대해 이미지·저장·업로드·게시를 수행합니다.

    `generate`/`auto`/`column` 명령이 공유하는 후처리 파이프라인입니다.
    """
    # 프로젝트 디렉토리 생성
    project_dir = _make_project_dir(config.output_dir, content.topic)

    output = ProjectOutput(project_dir=project_dir, content=content)

    # 2) 이미지 생성
    if content.image_prompt and not no_image:
        img_gen = ImageGenerator(config.xai)
        images = img_gen.generate(
            prompt=content.image_prompt,
            output_dir=project_dir,
            filename="post_image.png",
        )
        output.images = images

    # 3) 파일 저장
    _save_content_files(project_dir, output)

    # 4) Google Drive 업로드
    if upload:
        if config.gdrive is None:
            console.print(
                "[bold red]❌ Google Drive 설정이 없습니다.[/]\n"
                "   GOOGLE_SA_KEY_PATH, GDRIVE_FOLDER_ID 환경변수를 설정하세요."
            )
        else:
            from xp.gdrive_uploader import GDriveUploader

            uploader = GDriveUploader(config.gdrive)
            output.uploads = uploader.upload_directory(project_dir)

    # 5) X 게시
    if post:
        image_paths = [] if no_image else [img.local_path for img in output.images]
        try:
            output.posts = _post_content(config, content, image_paths, method)
            _log_post_result(topic, output.posts)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[bold red]❌ 게시 실패(생성 결과는 저장됨): {exc}[/]")
            _log_post_result(topic, None, error=str(exc))

    return output


def cmd_generate(args: argparse.Namespace) -> None:
    """글 + 이미지를 생성합니다."""
    config = load_config()

    keywords = args.keywords.split(",") if args.keywords else None
    output = _generate_pipeline(
        config,
        topic=args.topic,
        post_type=PostType(args.type),
        keywords=keywords,
        tone=args.tone,
        extra=args.extra,
        no_image=args.no_image,
        upload=args.upload,
        post=args.post,
        method=args.method,
    )

    _print_result(output)


# ──────────────────────────────────────────────
# column 명령 — 리서치 md 기반 장문 칼럼
# ──────────────────────────────────────────────


def _column_sources(config: AppConfig, file_arg: str | None) -> list[Path]:
    """처리할 리서치 md 파일 목록을 결정합니다.

    --file이 있으면 그 파일만, 없으면 입력 폴더의 *.md 전부(processed 제외).
    """
    if file_arg:
        p = Path(file_arg)
        if not p.is_file():
            console.print(f"[bold red]❌ 파일을 찾을 수 없습니다: {p}[/]")
            sys.exit(1)
        return [p]

    input_dir = config.input_dir
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
        console.print(
            f"[yellow]입력 폴더를 만들었습니다: {input_dir}\n"
            f"  여기에 리서치 .md 파일을 넣고 다시 실행하세요.[/]"
        )
        return []

    files = sorted(
        p for p in input_dir.glob("*.md") if p.parent.name != "processed"
    )
    if not files:
        console.print(
            f"[yellow]{input_dir}에 처리할 .md 파일이 없습니다. "
            f"리서치 파일을 넣고 다시 실행하세요.[/]"
        )
    return files


def cmd_column(args: argparse.Namespace) -> None:
    """리서치 md 파일을 장문 칼럼으로 작성합니다."""
    config = load_config()
    sources = _column_sources(config, args.file)
    if not sources:
        return

    gen = ContentGenerator(config.xai)

    for src in sources:
        console.print(f"\n[bold]📄 리서치 파일:[/] {src}")
        research = src.read_text(encoding="utf-8").strip()
        if not research:
            console.print(f"[yellow]⚠️  빈 파일, 건너뜁니다: {src}[/]")
            continue

        content = gen.generate_column(
            research,
            title=args.title,
            tone=args.tone,
            extra=args.extra,
        )
        output = _finalize_output(
            config,
            content,
            no_image=args.no_image,
            upload=args.upload,
            post=args.post,
            method=args.method,
        )
        _print_result(output)

        # 원본을 processed/로 이동 (입력 폴더에서 온 경우에만)
        if args.file is None and not args.keep:
            processed = config.input_dir / "processed"
            processed.mkdir(parents=True, exist_ok=True)
            dest = processed / src.name
            if dest.exists():
                dest = processed / f"{src.stem}-{datetime.now():%H%M%S}{src.suffix}"
            src.rename(dest)
            console.print(f"[dim]→ 처리 완료, 원본 이동: {dest}[/]")


# ──────────────────────────────────────────────
# topics 명령
# ──────────────────────────────────────────────


def cmd_topics(args: argparse.Namespace) -> None:
    """Grok에게 최신 화제 기반 포스팅 주제를 제안받습니다."""
    config = load_config()

    from xp.topic_finder import TopicFinder

    avoid = _recent_topics(config.output_dir, args.avoid_recent)
    finder = TopicFinder(config.xai)
    topics = finder.suggest(count=args.count, avoid_topics=avoid, category=args.category)

    table = Table(title="제안된 주제")
    table.add_column("주제", style="cyan")
    table.add_column("키워드", style="yellow")
    table.add_column("추천 이유", style="dim")

    for t in topics:
        table.add_row(t.topic, ", ".join(t.keywords), t.reason or "-")

    console.print(table)


# ──────────────────────────────────────────────
# auto 명령 — 주제 자동 선정 + 생성(+업로드/게시), cron/스케줄러용
# ──────────────────────────────────────────────


def cmd_auto(args: argparse.Namespace) -> None:
    """주제를 자동으로 선정해 생성(+업로드/게시)까지 수행합니다.

    사람 개입 없이 스케줄러(cron 등)에서 주기적으로 실행하도록 설계되었습니다.
    """
    config = load_config()

    from xp.topic_finder import TopicFinder

    avoid = _recent_topics(config.output_dir, args.avoid_recent)
    finder = TopicFinder(config.xai)
    topics = finder.suggest(count=1, avoid_topics=avoid, category=args.category)
    chosen = topics[0]

    console.print(f"[bold cyan]📌 선정된 주제:[/] {chosen.topic}")

    post_type = (
        PostType(random.choice(["single", "thread"]))
        if args.type == "random"
        else PostType(args.type)
    )

    output = _generate_pipeline(
        config,
        topic=chosen.topic,
        post_type=post_type,
        keywords=chosen.keywords or None,
        tone=args.tone,
        extra=args.extra,
        no_image=args.no_image,
        upload=args.upload,
        post=args.post,
        method=args.method,
    )

    _print_result(output)


# ──────────────────────────────────────────────
# schedule 명령 — OS 스케줄러 등록 안내
# ──────────────────────────────────────────────


def cmd_schedule(args: argparse.Namespace) -> None:
    """`xp auto`를 OS 스케줄러에 등록하는 방법을 안내합니다.

    시스템 crontab/작업 스케줄러를 직접 변경하지 않고, 등록할 명령을 출력만 합니다.
    """
    project_root = Path(__file__).resolve().parent.parent
    python_bin = sys.executable

    auto_args = ["-m", "xp", "auto"]
    if args.upload:
        auto_args.append("--upload")
    if args.post:
        auto_args.append("--post")
        auto_args.extend(["--method", args.method])
    if args.category:
        auto_args.extend(["--category", args.category])

    auto_cmd = " ".join([python_bin, *auto_args])
    log_path = project_root / "xp-auto.log"

    if args.every_hour:
        jitter_seconds = max(0, args.jitter_minutes) * 60
        cron_line = (
            f'0 * * * * cd "{project_root}" && sleep $(python3 -c '
            f'"import random;print(random.randint(0,{jitter_seconds}))") '
            f'&& {auto_cmd} >> "{log_path}" 2>&1'
        )
        console.print(
            Panel(
                f"[bold]매시 정각에 트리거되지만, 실제 실행은 0~{args.jitter_minutes}분 "
                f"사이 무작위로 지연되어 매번 다른 시각에 게시됩니다 (봇 탐지 회피용).[/]\n\n"
                f"1) 편집기 열기: [cyan]crontab -e[/]\n"
                f"2) 아래 줄 추가:\n\n"
                f"[green]{cron_line}[/]\n",
                title="🕒 cron (macOS/Linux) — 매시간 + 랜덤 지연",
                border_style="cyan",
            )
        )

        ps_sleep = f"Start-Sleep -Seconds (Get-Random -Maximum {jitter_seconds})"
        schtasks_cmd = (
            f'schtasks /create /tn "XP AutoPost" '
            f'/tr "powershell -Command \\"{ps_sleep}; {auto_cmd}\\"" '
            f'/sc hourly /mo 1'
        )
        console.print(
            Panel(
                f"관리자 권한 PowerShell/CMD에서 아래 명령을 실행하세요:\n\n"
                f"[green]{schtasks_cmd}[/]\n",
                title="🕒 Windows 작업 스케줄러 — 매시간 + 랜덤 지연",
                border_style="cyan",
            )
        )
    else:
        hour, minute = args.hour, args.minute
        cron_line = (
            f'{minute} {hour} * * * cd "{project_root}" && {auto_cmd} '
            f'>> "{log_path}" 2>&1'
        )

        console.print(
            Panel(
                f"[bold]매일 {hour:02d}:{minute:02d}에 자동 실행하려면 아래 crontab 항목을 추가하세요.[/]\n\n"
                f"1) 편집기 열기: [cyan]crontab -e[/]\n"
                f"2) 아래 줄 추가:\n\n"
                f"[green]{cron_line}[/]\n",
                title="🕒 cron (macOS/Linux)",
                border_style="cyan",
            )
        )

        schtasks_cmd = (
            f'schtasks /create /tn "XP AutoPost" /tr "{auto_cmd}" '
            f'/sc daily /st {hour:02d}:{minute:02d}'
        )
        console.print(
            Panel(
                f"관리자 권한 PowerShell/CMD에서 아래 명령을 실행하세요:\n\n"
                f"[green]{schtasks_cmd}[/]\n",
                title="🕒 Windows 작업 스케줄러",
                border_style="cyan",
            )
        )

    console.print(
        "[dim]※ 이 명령은 등록 방법을 안내만 합니다. 실제 등록은 사용자가 직접 수행하세요.[/]"
    )


# ──────────────────────────────────────────────
# upload 명령
# ──────────────────────────────────────────────


def cmd_upload(args: argparse.Namespace) -> None:
    """이미 생성된 디렉토리를 Google Drive에 업로드합니다."""
    config = load_config()

    if config.gdrive is None:
        console.print(
            "[bold red]❌ Google Drive 설정이 없습니다.[/]\n"
            "   GOOGLE_SA_KEY_PATH, GDRIVE_FOLDER_ID 환경변수를 설정하세요."
        )
        sys.exit(1)

    from xp.gdrive_uploader import GDriveUploader

    local_dir = Path(args.dir)
    if not local_dir.is_dir():
        console.print(f"[bold red]❌ 디렉토리를 찾을 수 없습니다: {local_dir}[/]")
        sys.exit(1)

    uploader = GDriveUploader(config.gdrive)
    results = uploader.upload_directory(local_dir, subfolder=args.subfolder)

    table = Table(title="업로드 결과")
    table.add_column("파일명", style="cyan")
    table.add_column("파일 ID", style="dim")
    table.add_column("링크", style="green")

    for r in results:
        table.add_row(r.file_name, r.file_id, r.web_view_link or "-")

    console.print(table)


# ──────────────────────────────────────────────
# post 명령 — Phase 2
# ──────────────────────────────────────────────


def _load_content(project_dir: Path) -> GeneratedContent:
    """프로젝트 디렉토리의 meta.json에서 콘텐츠를 복원합니다."""
    meta_path = project_dir / "meta.json"
    if not meta_path.exists():
        console.print(f"[bold red]❌ meta.json을 찾을 수 없습니다: {meta_path}[/]")
        sys.exit(1)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return GeneratedContent.model_validate(meta)


def _collect_images(project_dir: Path) -> list[Path]:
    """프로젝트 디렉토리의 이미지 파일을 정렬해 반환합니다."""
    images: list[Path] = []
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        images.extend(project_dir.glob(pattern))
    return sorted(images)


def _post_content(config, content, images, method: str):
    """지정한 방식으로 게시합니다.

    method:
        api     - X API (tweepy)
        browser - DrissionPage 브라우저 자동화 (비상용)
        auto    - API 먼저 시도, 실패 시 브라우저로 폴백
    """
    def _api():
        if config.xapi is None:
            raise RuntimeError(
                "X API 설정이 없습니다. X_CONSUMER_KEY 등 4개 환경변수를 설정하세요."
            )
        from xp.x_poster import XPoster

        return XPoster(config.xapi).post_content(content, images)

    def _browser():
        from xp.x_poster_browser import BrowserXPoster

        return BrowserXPoster().post_content(content, images)

    if method == "browser":
        return _browser()
    if method == "api":
        return _api()

    # auto: API 먼저, 실패하면 브라우저로 폴백
    try:
        return _api()
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]⚠️  API 게시 실패({exc}). 브라우저 비상 경로로 폴백합니다...[/]"
        )
        return _browser()


def cmd_post(args: argparse.Namespace) -> None:
    """생성된 콘텐츠를 X에 게시합니다."""
    config = load_config()

    project_dir = Path(args.dir)
    if not project_dir.is_dir():
        console.print(f"[bold red]❌ 디렉토리를 찾을 수 없습니다: {project_dir}[/]")
        sys.exit(1)

    content = _load_content(project_dir)
    images = [] if args.no_image else _collect_images(project_dir)

    try:
        results = _post_content(config, content, images, args.method)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]❌ 게시 실패: {exc}[/]")
        sys.exit(1)

    table = Table(title="X 게시 결과")
    table.add_column("트윗 ID", style="dim")
    table.add_column("URL", style="green")
    table.add_column("본문", style="white")

    for r in results:
        preview = r.text.replace("\n", " ")
        table.add_row(r.tweet_id, r.url, preview[:40])

    console.print(table)


# ──────────────────────────────────────────────
# article 명령 — X 아티클(배너형 롱폼) 반자동 발행 준비
# ──────────────────────────────────────────────


def _copy_to_clipboard(text: str) -> bool:
    """본문을 OS 클립보드에 복사합니다 (best-effort, 실패해도 무해)."""
    import subprocess

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                input=text, text=True, encoding="utf-8", check=True,
            )
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"], input=text, text=True, check=True
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def _prepare_article_cover(project_dir: Path) -> Path | None:
    """아티클 배너용 5:2 커버 이미지를 준비합니다.

    X 아티클 커버 규격은 5:2이므로, 생성된 16:9 이미지를 5:2로 크롭한 별도
    커버(cover_5x2.png)를 만든다. 크롭으로 잘려나간 워터마크는 다시 합성한다.
    """
    import shutil

    from xp.image_generator import add_watermark, fit_aspect_ratio

    src = project_dir / "post_image.png"
    if not src.exists():
        return None
    cover = project_dir / "cover_5x2.png"
    try:
        shutil.copyfile(src, cover)
        fit_aspect_ratio(cover, 5 / 2)  # 5:2 배너 비율
        add_watermark(cover)
        return cover
    except Exception:  # noqa: BLE001
        return src


def _article_from_dir(project_dir: Path, *, post: bool, review: bool) -> None:
    """이미 생성된 칼럼 디렉토리를 아티클로 준비(+선택 발행)합니다."""
    content = _load_content(project_dir)
    if content.tweet is None:
        console.print(f"[bold red]❌ 본문이 없는 프로젝트입니다: {project_dir}[/]")
        return

    title = content.topic
    full = content.tweet.text.strip()
    lines = full.splitlines()
    # 첫 줄이 제목과 사실상 같으면 본문에서 제외한다.
    if lines and lines[0].strip() == title.strip():
        body = "\n".join(lines[1:]).lstrip("\n")
    else:
        body = full
    # X 아티클(DraftJS)은 빈 줄마다 빈 블록을 만들어 간격이 두 배가 된다.
    # 문단 간격은 아티클이 알아서 주므로, 빈 줄을 단일 줄바꿈으로 정리한다.
    body = re.sub(r"\n{2,}", "\n", body).strip()
    (project_dir / "body.txt").write_text(body, encoding="utf-8")

    cover = _prepare_article_cover(project_dir)
    cover_line = str(cover) if cover else "(없음 — --no-image로 생성됨)"
    copied = _copy_to_clipboard(body)

    # 브라우저 자동화로 아티클 작성기까지 조작 (셀렉터 미검증, 어시스트 방식)
    if post:
        from xp.x_poster_browser import BrowserXPoster

        console.print(
            "[yellow]※ X 아티클은 공개 API가 없어 브라우저로 작성기를 자동 조작합니다.[/]"
        )
        BrowserXPoster(headless=False).post_article(
            title,
            body,
            cover,
            publish=not review,
            hold_for_review=review,
            debug_dir=project_dir,
        )
        return

    console.print(
        Panel(
            f"[bold]제목:[/] {title}\n"
            f"[bold]커버 이미지:[/] {cover_line}\n"
            f"[bold]본문:[/] {project_dir / 'body.txt'} "
            f"({len(body)}자){'  [green]— 클립보드에 복사됨[/]' if copied else ''}",
            title="📰 X 아티클 발행 준비 완료",
            border_style="green",
        )
    )
    console.print(
        "[bold]붙여넣기 순서[/] (X는 아티클 API가 없어 작성기에서 직접 발행합니다):\n"
        "  1) X 웹 → 글쓰기 창의 [cyan]‘아티클 작성(Write Article)’[/] 진입\n"
        "     (X Premium+ 필요)\n"
        "  2) 커버(헤더) 이미지 업로드 → 위 post_image.png\n"
        "  3) 제목 칸에 위 제목 붙여넣기\n"
        f"  4) 본문에 붙여넣기 — 이미 클립보드에 있음{' ✅' if copied else ' (body.txt에서 복사)'}\n"
        "  5) 검토 후 게시"
    )


def cmd_article(args: argparse.Namespace) -> None:
    """리서치 md(또는 기존 칼럼 디렉토리)를 X 아티클로 준비/발행합니다.

    --dir 를 주면 이미 만든 칼럼 폴더를 아티클로 처리하고, 없으면 input/ 폴더의
    md(또는 --file)를 읽어 칼럼을 생성한 뒤 곧바로 아티클로 이어갑니다.

    X 아티클(상단 배너 + 리치서식)은 공개 API가 없어, 준비(제목/본문 분리 +
    클립보드 복사) 후 붙여넣거나 --post로 브라우저 작성기를 조작해 발행합니다.
    """
    config = load_config()

    # 1) --dir 지정 시: 기존 칼럼 폴더를 그대로 사용
    if args.dir:
        project_dir = Path(args.dir)
        if not project_dir.is_dir():
            console.print(f"[bold red]❌ 디렉토리를 찾을 수 없습니다: {project_dir}[/]")
            sys.exit(1)
        _article_from_dir(project_dir, post=args.post, review=args.review)
        return

    # 2) 그 외: input/ 폴더의 md(또는 --file)로 칼럼을 생성한 뒤 아티클로
    sources = _column_sources(config, args.file)
    if not sources:
        return

    gen = ContentGenerator(config.xai)
    for src in sources:
        console.print(f"\n[bold]📄 리서치 파일:[/] {src}")
        research = src.read_text(encoding="utf-8").strip()
        if not research:
            console.print(f"[yellow]⚠️  빈 파일, 건너뜁니다: {src}[/]")
            continue

        content = gen.generate_column(
            research, title=args.title, tone=args.tone, extra=args.extra
        )
        output = _finalize_output(
            config, content,
            no_image=args.no_image, upload=False, post=False, method="api",
        )
        _print_result(output)

        _article_from_dir(output.project_dir, post=args.post, review=args.review)

        # 원본을 processed/로 이동 (입력 폴더에서 온 경우에만)
        if args.file is None and not args.keep:
            processed = config.input_dir / "processed"
            processed.mkdir(parents=True, exist_ok=True)
            dest = processed / src.name
            if dest.exists():
                dest = processed / f"{src.stem}-{datetime.now():%H%M%S}{src.suffix}"
            src.rename(dest)
            console.print(f"[dim]→ 처리 완료, 원본 이동: {dest}[/]")


# ──────────────────────────────────────────────
# list 명령
# ──────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> None:
    """생성 히스토리를 출력합니다."""
    config = load_config()
    output_dir = config.output_dir

    if not output_dir.exists():
        console.print("[dim]생성된 콘텐츠가 없습니다.[/]")
        return

    table = Table(title="XP 생성 히스토리")
    table.add_column("폴더", style="cyan")
    table.add_column("유형", style="yellow")
    table.add_column("주제", style="white")
    table.add_column("이미지", style="green")
    table.add_column("업로드", style="blue")

    for d in sorted(output_dir.iterdir(), reverse=True):
        if not d.is_dir():
            continue

        meta_path = d / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            post_type = meta.get("post_type", "?")
            topic = meta.get("topic", "?")
        else:
            post_type = "?"
            topic = "?"

        has_images = "✅" if any(d.glob("*.png")) or any(d.glob("*.jpg")) else "❌"
        has_upload = "?"  # 추후 업로드 기록과 연동

        table.add_row(d.name, post_type, topic[:30], has_images, has_upload)

    console.print(table)


# ──────────────────────────────────────────────
# 결과 출력
# ──────────────────────────────────────────────


def _print_result(output: ProjectOutput) -> None:
    """생성 결과를 보기 좋게 출력합니다."""
    content = output.content

    console.print()
    console.print(
        Panel(
            f"[bold]주제:[/] {content.topic}\n"
            f"[bold]유형:[/] {content.post_type.value}\n"
            f"[bold]모델:[/] {content.model_used}\n"
            f"[bold]저장:[/] {output.project_dir}",
            title="📋 생성 결과",
            border_style="green",
        )
    )

    if content.tweet:
        is_column = content.post_type == PostType.COLUMN
        console.print(
            Panel(
                content.tweet.full_text,
                title="📰 칼럼" if is_column else "🐦 트윗",
                border_style="cyan",
            )
        )

    if content.thread:
        for i, t in enumerate(content.thread.tweets, 1):
            console.print(
                Panel(
                    t.full_text,
                    title=f"🧵 스레드 {i}/{content.thread.tweet_count}",
                    border_style="cyan",
                )
            )

    if output.images:
        console.print(f"\n[bold green]🖼️  이미지 {len(output.images)}장 생성됨[/]")
        for img in output.images:
            console.print(f"   📁 {img.local_path}")

    if output.uploads:
        console.print(f"\n[bold blue]☁️  Google Drive에 {len(output.uploads)}개 업로드됨[/]")
        for u in output.uploads:
            console.print(f"   🔗 {u.web_view_link or u.file_id}")

    if output.posts:
        console.print(f"\n[bold magenta]🐦 X에 {len(output.posts)}개 게시됨[/]")
        for post in output.posts:
            console.print(f"   🔗 {post.url}")

    console.print()


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
# grok-login 명령
# ──────────────────────────────────────────────


def cmd_x_browser_login(args: argparse.Namespace) -> None:
    """비상용 브라우저 게시를 위해 x.com에 1회 수동 로그인합니다."""
    from xp.x_poster_browser import BrowserXPoster

    console.print("[bold cyan]브라우저 비상 게시용 X 로그인을 시작합니다...[/]")
    BrowserXPoster(headless=False).open_login()


def cmd_grok_login(args: argparse.Namespace) -> None:
    """X OAuth 디바이스 코드 로그인을 수행합니다."""
    from xp.grok_oauth import login

    console.print("[bold cyan]Grok OAuth 로그인을 시작합니다...[/]")
    token_path = login()
    console.print(
        f"[bold green]✅ 로그인 완료![/]\n"
        f"   토큰 저장 위치: {token_path}\n"
        f"   이제 generate 명령을 바로 사용할 수 있습니다."
    )


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────


def main() -> None:
    """CLI 엔트리포인트."""
    parser = argparse.ArgumentParser(
        prog="xp",
        description="XP - X 포스팅 자동화 (Grok API + Google Drive)",
    )
    subparsers = parser.add_subparsers(dest="command", help="사용할 명령")

    # ── grok-login ──
    p_login = subparsers.add_parser("grok-login", help="X OAuth 로그인 (최초 1회)")
    p_login.set_defaults(func=cmd_grok_login)

    # ── x-browser-login ── (비상 게시용)
    p_blogin = subparsers.add_parser(
        "x-browser-login", help="비상용 브라우저 게시를 위한 X 로그인 (최초 1회)"
    )
    p_blogin.set_defaults(func=cmd_x_browser_login)

    # ── generate ──
    p_gen = subparsers.add_parser("generate", help="글 + 이미지 생성")
    p_gen.add_argument("--topic", "-t", required=True, help="포스팅 주제")
    p_gen.add_argument(
        "--type",
        choices=["single", "thread"],
        default="single",
        help="포스팅 유형 (기본: single)",
    )
    p_gen.add_argument("--keywords", "-k", help="키워드 (쉼표 구분)")
    p_gen.add_argument("--tone", help="글의 톤 (예: 전문적, 유머러스)")
    p_gen.add_argument("--extra", help="추가 지시 사항")
    p_gen.add_argument(
        "--no-image", action="store_true", help="이미지 생성 생략"
    )
    p_gen.add_argument(
        "--upload", "-u", action="store_true", help="Google Drive에 업로드"
    )
    p_gen.add_argument(
        "--post", "-p", action="store_true", help="생성 직후 X에 바로 게시"
    )
    p_gen.add_argument(
        "--method",
        choices=["api", "browser", "auto"],
        default="api",
        help="게시 방식: api(기본) / browser(비상) / auto(API 실패 시 브라우저 폴백)",
    )
    p_gen.set_defaults(func=cmd_generate)

    # ── column ── (리서치 md 기반 장문 칼럼)
    p_col = subparsers.add_parser(
        "column", help="리서치 md 파일을 장문(칼럼)으로 작성"
    )
    p_col.add_argument(
        "--file", "-f",
        help="리서치 md 파일 경로 (생략 시 입력 폴더의 *.md 전부 처리)",
    )
    p_col.add_argument("--title", help="칼럼 제목 강제 지정 (생략 시 모델이 생성)")
    p_col.add_argument("--tone", help="글의 톤 (예: 분석적, 논쟁적)")
    p_col.add_argument("--extra", help="추가 지시 사항")
    p_col.add_argument("--no-image", action="store_true", help="헤더 이미지 생략")
    p_col.add_argument(
        "--upload", "-u", action="store_true", help="Google Drive에 업로드"
    )
    p_col.add_argument(
        "--post", "-p", action="store_true", help="생성 직후 X에 바로 게시"
    )
    p_col.add_argument(
        "--method",
        choices=["api", "browser", "auto"],
        default="api",
        help="게시 방식: api(기본) / browser(비상) / auto(폴백)",
    )
    p_col.add_argument(
        "--keep",
        action="store_true",
        help="처리 후 입력 md를 processed/로 이동하지 않고 그대로 둠",
    )
    p_col.set_defaults(func=cmd_column)

    # ── topics ──
    p_topics = subparsers.add_parser(
        "topics", help="Grok에게 최신 화제 기반 포스팅 주제 제안받기"
    )
    p_topics.add_argument(
        "--count", "-n", type=int, default=5, help="제안받을 주제 개수 (기본: 5)"
    )
    p_topics.add_argument("--category", help="주제 분야 (예: AI, 경제, 스포츠)")
    p_topics.add_argument(
        "--avoid-recent",
        type=int,
        default=20,
        help="최근 생성된 몇 개 주제와 겹치지 않게 할지 (기본: 20)",
    )
    p_topics.set_defaults(func=cmd_topics)

    # ── auto ── (주제 자동 선정 + 생성, cron/스케줄러용)
    p_auto = subparsers.add_parser(
        "auto", help="주제 자동 선정 -> 생성(+업로드/게시), 스케줄러에서 실행용"
    )
    p_auto.add_argument("--category", help="주제 분야 (예: AI, 경제, 스포츠)")
    p_auto.add_argument(
        "--avoid-recent",
        type=int,
        default=20,
        help="최근 생성된 몇 개 주제와 겹치지 않게 할지 (기본: 20)",
    )
    p_auto.add_argument(
        "--type",
        choices=["single", "thread", "random"],
        default="single",
        help="포스팅 유형 (기본: single, random 선택 시 무작위)",
    )
    p_auto.add_argument("--tone", help="글의 톤 (예: 전문적, 유머러스)")
    p_auto.add_argument("--extra", help="추가 지시 사항")
    p_auto.add_argument("--no-image", action="store_true", help="이미지 생성 생략")
    p_auto.add_argument(
        "--upload", "-u", action="store_true", help="Google Drive에 업로드"
    )
    p_auto.add_argument(
        "--post", "-p", action="store_true", help="생성 직후 X에 바로 게시"
    )
    p_auto.add_argument(
        "--method",
        choices=["api", "browser", "auto"],
        default="api",
        help="게시 방식: api(기본) / browser(비상) / auto(API 실패 시 브라우저 폴백)",
    )
    p_auto.set_defaults(func=cmd_auto)

    # ── schedule ── (OS 스케줄러 등록 안내)
    p_sched = subparsers.add_parser(
        "schedule", help="`xp auto`를 OS 스케줄러에 등록하는 방법 안내"
    )
    p_sched.add_argument(
        "--hour", type=int, default=9, help="실행 시각(시), 24시간제 (기본: 9)"
    )
    p_sched.add_argument("--minute", type=int, default=0, help="실행 시각(분) (기본: 0)")
    p_sched.add_argument(
        "--every-hour",
        action="store_true",
        help="매일 1회 대신 매시간 실행 (실행 시각은 --jitter-minutes 범위 내 무작위)",
    )
    p_sched.add_argument(
        "--jitter-minutes",
        type=int,
        default=50,
        help="--every-hour 사용 시, 정각 이후 0~N분 사이 무작위 지연 (기본: 50)",
    )
    p_sched.add_argument("--category", help="주제 분야 (예: AI, 경제, 스포츠)")
    p_sched.add_argument(
        "--upload", "-u", action="store_true", help="Google Drive 업로드도 포함"
    )
    p_sched.add_argument(
        "--post", "-p", action="store_true", help="X 게시도 포함"
    )
    p_sched.add_argument(
        "--method",
        choices=["api", "browser", "auto"],
        default="api",
        help="게시 방식 (기본: api)",
    )
    p_sched.set_defaults(func=cmd_schedule)

    # ── upload ──
    p_up = subparsers.add_parser("upload", help="기존 파일을 Google Drive에 업로드")
    p_up.add_argument("--dir", "-d", required=True, help="업로드할 디렉토리")
    p_up.add_argument("--subfolder", help="GDrive 서브폴더명 (기본: 날짜 기반)")
    p_up.set_defaults(func=cmd_upload)

    # ── post ── (Phase 2)
    p_post = subparsers.add_parser("post", help="생성된 콘텐츠를 X에 게시")
    p_post.add_argument("--dir", "-d", required=True, help="게시할 프로젝트 디렉토리")
    p_post.add_argument(
        "--no-image", action="store_true", help="이미지 첨부 생략"
    )
    p_post.add_argument(
        "--method",
        choices=["api", "browser", "auto"],
        default="api",
        help="게시 방식: api(기본) / browser(비상) / auto(API 실패 시 브라우저 폴백)",
    )
    p_post.set_defaults(func=cmd_post)

    # ── article ── (input md → 칼럼 → X 아티클 준비/발행)
    p_art = subparsers.add_parser(
        "article",
        help="리서치 md(input/) 또는 기존 칼럼 폴더를 X 아티클로 준비/발행",
    )
    p_art.add_argument(
        "--dir", "-d",
        help="기존 칼럼 프로젝트 디렉토리 (생략 시 input/ 의 md로 새로 생성)",
    )
    p_art.add_argument(
        "--file", "-f",
        help="리서치 md 파일 (생략 시 input/ 의 *.md 전부, --dir 없을 때만)",
    )
    p_art.add_argument("--title", help="칼럼 제목 강제 지정")
    p_art.add_argument("--tone", help="글의 톤")
    p_art.add_argument("--extra", help="추가 지시 사항")
    p_art.add_argument("--no-image", action="store_true", help="커버 이미지 생성 생략")
    p_art.add_argument(
        "--keep", action="store_true", help="처리 후 input md를 이동하지 않음"
    )
    p_art.add_argument(
        "--post", "-p",
        action="store_true",
        help="브라우저로 아티클 작성기까지 조작해 발행 (X Premium+, 최초 x-browser-login 필요)",
    )
    p_art.add_argument(
        "--review",
        action="store_true",
        help="--post 시 마지막 게시는 누르지 않고 검토용으로 멈춤",
    )
    p_art.set_defaults(func=cmd_article)

    # ── list ──
    p_list = subparsers.add_parser("list", help="생성 히스토리 보기")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)

