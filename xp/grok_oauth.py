"""xAI(Grok) OAuth 2.0 device-code login.

API 키 없이 SuperGrok / X Premium+ 구독 계정으로 로그인해
access token을 발급받고, 만료 전 refresh token으로 자동 갱신한다.

NaverBlog 파이프라인의 grok_oauth.py와 동일한 토큰 파일을 공유하므로,
이미 로그인한 적이 있으면 별도 재로그인 없이 바로 사용 가능하다.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ISSUER = "https://auth.x.ai"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
DEVICE_CODE_URL = f"{ISSUER}/oauth2/device/code"
FALLBACK_TOKEN_URL = f"{ISSUER}/oauth2/token"
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"

# access token은 약 6시간 유효 - 1시간 여유를 두고 미리 갱신한다.
REFRESH_SKEW_SECONDS = 3600

# NaverBlog 파이프라인과 동일한 토큰 경로를 공유한다.
DEFAULT_TOKEN_PATH = Path.home() / ".naverblog" / "grok_oauth.json"


def login(token_path: Path = DEFAULT_TOKEN_PATH) -> Path:
    """브라우저 기반 device-code 로그인을 수행합니다."""
    status, device = _post_form(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if status != 200 or "device_code" not in device:
        raise RuntimeError(f"Grok 디바이스 코드 발급 실패 (HTTP {status}): {device}")

    verification_url = device.get("verification_uri_complete") or device.get("verification_uri", "")
    print(f"브라우저에서 로그인을 승인하세요: {verification_url}")
    print(f"확인 코드: {device.get('user_code', '')}")
    try:
        webbrowser.open(verification_url)
    except Exception:
        pass

    tokens = _poll_device_token(
        _token_endpoint(),
        device_code=device["device_code"],
        expires_in=int(device.get("expires_in", 600)),
        interval=int(device.get("interval", 5)),
    )
    _save_tokens(token_path, tokens)
    print(f"로그인 완료. 토큰 저장: {token_path}")
    return token_path


def get_access_token(token_path: Path = DEFAULT_TOKEN_PATH, force_refresh: bool = False) -> str:
    """저장된 토큰을 반환합니다. 만료 임박 시 자동 갱신합니다."""
    if not Path(token_path).exists():
        raise RuntimeError(
            "Grok OAuth 토큰이 없습니다. 먼저 로그인하세요: python -m xp grok-login"
        )
    state = json.loads(Path(token_path).read_text(encoding="utf-8"))
    expires_at = float(state.get("expires_at", 0))
    if force_refresh or time.time() >= expires_at - REFRESH_SKEW_SECONDS:
        state = _refresh(state)
        _write_state(token_path, state)
    return state["access_token"]


def _refresh(state: dict) -> dict:
    """refresh token으로 access token을 갱신합니다."""
    refresh_token = str(state.get("refresh_token", "") or "").strip()
    if not refresh_token:
        raise RuntimeError("refresh_token이 없습니다. 다시 로그인하세요: python -m xp grok-login")
    status, payload = _post_form(
        _token_endpoint(),
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
    )
    if status == 403:
        raise RuntimeError(
            "Grok 토큰 갱신이 403으로 거부되었습니다. 이 계정은 OAuth API 사용 등급이 아닐 수 있습니다. "
            "SuperGrok / X Premium+ 구독 등급을 확인한 뒤 다시 로그인하세요: python -m xp grok-login"
        )
    if status != 200 or "access_token" not in payload:
        raise RuntimeError(
            f"Grok 토큰 갱신 실패 (HTTP {status}): {payload}. 다시 로그인하세요: python -m xp grok-login"
        )
    merged = dict(state)
    merged.update(payload)
    if not payload.get("refresh_token"):
        merged["refresh_token"] = refresh_token
    merged["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
    return merged


def _poll_device_token(token_endpoint: str, *, device_code: str, expires_in: int, interval: int) -> dict:
    """디바이스 코드 승인을 폴링합니다."""
    deadline = time.monotonic() + max(1, expires_in)
    current_interval = max(1, interval)
    while time.monotonic() < deadline:
        status, payload = _post_form(
            token_endpoint,
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_code,
            },
        )
        if status == 200 and payload.get("access_token"):
            if not payload.get("refresh_token"):
                raise RuntimeError("토큰 응답에 refresh_token이 없습니다.")
            return payload
        error_code = str(payload.get("error") or "")
        if error_code == "authorization_pending":
            time.sleep(current_interval)
            continue
        if error_code == "slow_down":
            current_interval = min(current_interval + 1, 30)
            time.sleep(current_interval)
            continue
        description = payload.get("error_description") or payload.get("error") or payload
        raise RuntimeError(f"Grok 디바이스 로그인 실패: {description}")
    raise RuntimeError("로그인 승인 대기 시간이 초과되었습니다. 다시 시도하세요.")


def _token_endpoint() -> str:
    """OpenID Discovery에서 token endpoint를 가져옵니다."""
    try:
        request = urllib.request.Request(DISCOVERY_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        endpoint = str(data.get("token_endpoint", "") or "")
        if endpoint.startswith(ISSUER):
            return endpoint
    except Exception:
        pass
    return FALLBACK_TOKEN_URL


def _save_tokens(token_path: Path, tokens: dict) -> None:
    state = dict(tokens)
    state["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
    _write_state(token_path, state)


def _write_state(token_path: Path, state: dict) -> None:
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _post_form(url: str, data: dict[str, str]) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": "http_error", "error_description": str(exc)}
