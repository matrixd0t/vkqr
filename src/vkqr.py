from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import re
import secrets
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0"
ACCEPT_LANGUAGE = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"

ID_HOST = "https://id.vk.ru"
API_HOST = "https://api.vk.ru"
LOGIN_HOST = "https://login.vk.ru"
WEB_HOST = "https://vk.ru"

APP_ID = 7913379
V_GET_QR_AUTH_DATA = "5.276"
V_AUTH_METHODS = "5.126"
V_USERS_GET = "5.199"

DEFAULT_POLL_INTERVAL = 2.0
MAX_CODE_ATTEMPTS = 5

STATUS_APPROVED = 2
STATUS_DECLINED = 3
STATUS_EXPIRED = 4
STATUS_AWAIT_CODE = 5

# Момент, после которого считаем expires_in не абсолютным временем, а интервалом.
_ABSOLUTE_TIMESTAMP_THRESHOLD = 1_000_000_000

_AUTH_PAGE_ACTION = base64.b64encode(
    json.dumps(
        {"name": "qr_auth", "token": "qr_auth_scanned", "entry": {"source": "main", "screen": "start"}},
        separators=(",", ":"),
    ).encode()
).decode()

_INIT_RE = re.compile(r"window\.init\s*=\s*")


class VkQrError(RuntimeError):
    def __init__(self, message: str, *, error_code: Any = None, raw: Any = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.raw = raw


class StoreError(VkQrError):
    pass


class Session:
    def __init__(
        self,
        uuid: str,
        anonymous_token: str,
        host_app_id: int = APP_ID,
        auth_url: str = "",
        auth_hash: str = "",
        deadline: float = 0.0,
    ) -> None:
        self.uuid = uuid
        self.anonymous_token = anonymous_token
        self.host_app_id = host_app_id
        self.auth_url = auth_url
        self.auth_hash = auth_hash
        self.deadline = deadline


def _safe_read(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:500]
    except Exception:
        return ""


class VkClient:
    """Минимальный HTTP-клиент на stdlib с общим cookie jar."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _open(self, request: urllib.request.Request):
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise VkQrError(
                f"HTTP {exc.code} при запросе {request.full_url}",
                raw=_safe_read(exc),
            ) from exc
        except urllib.error.URLError as exc:
            raise VkQrError(f"Сетевая ошибка: {exc.reason}") from exc

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, str]:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Language", ACCEPT_LANGUAGE)
        if body is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with self._open(request) as response:
            return response.status, response.read().decode("utf-8", "replace")

    def get(self, url: str, **kwargs: Any) -> tuple[int, str]:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> tuple[int, str]:
        return self._request("POST", url, **kwargs)

    def cookies(self) -> dict[str, str]:
        return {cookie.name: cookie.value for cookie in self.jar if cookie.value is not None}


def _api_headers() -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Origin": ID_HOST,
        "Referer": f"{ID_HOST}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }


def _parse_api(status: int, text: str, method: str) -> Any:
    try:
        payload: Any = json.loads(text)
    except ValueError as exc:
        raise VkQrError(f"{method}: некорректный ответ (HTTP {status})", raw=text[:500]) from exc
    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        if isinstance(error, dict):
            raise VkQrError(
                str(error.get("error_msg") or f"Ошибка {method}"),
                error_code=error.get("error_code"),
                raw=payload,
            )
    if isinstance(payload, dict) and "response" in payload:
        return payload["response"]
    return payload


def _extract_window_init(html: str) -> dict[str, Any]:
    match = _INIT_RE.search(html)
    if match is None:
        raise VkQrError("Не удалось найти window.init на странице VK ID (возможен анти-бот)")
    try:
        init = json.JSONDecoder().raw_decode(html[match.end():].lstrip())[0]
    except ValueError as exc:
        raise VkQrError("Не удалось разобрать window.init со страницы VK ID") from exc
    if not isinstance(init, dict) or not isinstance(init.get("auth"), dict):
        raise VkQrError("В window.init нет секции auth", raw=init)
    return init


def _generate_uuid(length: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(length))


def _absolute_url(url: str) -> str:
    prefix = f"{WEB_HOST}/"
    if url.startswith(prefix + "http"):
        url = url[len(prefix):]
    if not url.startswith(("http://", "https://")):
        url = f"{WEB_HOST}/{url.lstrip('/')}"
    return url


def _compute_deadline(expires_in: int) -> float:
    value = int(expires_in or 0)
    if value <= 0:
        return 0.0
    if value > _ABSOLUTE_TIMESTAMP_THRESHOLD:
        return float(value)
    return time.time() + value


def init_session(client: VkClient, uuid: Optional[str] = None) -> Session:
    """Открывает страницу VK ID и извлекает anonymous_token, как это делает браузер."""
    uuid = uuid or _generate_uuid()
    params = {
        "action": _AUTH_PAGE_ACTION,
        "scheme": "dark",
        "is_redesigned": "1",
        "response_type": "silent_token",
        "v": "1.3.0",
        "redirect_uri": f"{WEB_HOST}/",
        "uuid": uuid,
        "app_id": str(APP_ID),
    }
    _, html = client.get(
        f"{ID_HOST}/auth",
        params=params,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
    )
    init = _extract_window_init(html)
    auth = init["auth"]
    anonymous_token = auth.get("anonymous_token")
    if not anonymous_token:
        raise VkQrError("Страница VK ID не содержит anonymous_token", raw=auth)
    return Session(
        uuid=uuid,
        anonymous_token=str(anonymous_token),
        host_app_id=int(auth.get("host_app_id") or APP_ID),
    )


def get_qr_auth_data(client: VkClient, session: Session) -> Any:
    """auth.getQrAuthData — информация о приложении для экрана QR."""
    data = {
        "app_id": str(session.host_app_id),
        "origin": WEB_HOST,
        "app_settings": "",
        "uuid": session.uuid,
        "scope": "",
        "scope_string": "",
        "oauth_version": "",
        "scheme": "bright_light",
        "http_referer": "",
        "http_origin": "",
        "lang": "0",
        "access_token": "",
    }
    status, text = client.post(
        f"{API_HOST}/method/auth.getQrAuthData",
        params={"v": V_GET_QR_AUTH_DATA, "client_id": session.host_app_id},
        data=data,
        headers=_api_headers(),
    )
    return _parse_api(status, text, "auth.getQrAuthData")


def get_qr_code(client: VkClient, session: Session) -> Session:
    """auth.getAuthCode -> auth_url (содержимое QR) и auth_hash для поллинга."""
    data = {
        "device_name": "Windows NT 10.0; Win64; x64",
        "auth_code_flow": "0",
        "verification_hash": "",
        "force_regenerate": "0",
        "anonymous_token": session.anonymous_token,
        "is_switcher_flow": "",
        "access_token": "",
    }
    status, text = client.post(
        f"{API_HOST}/method/auth.getAuthCode",
        params={"v": V_AUTH_METHODS, "client_id": session.host_app_id},
        data=data,
        headers=_api_headers(),
    )
    payload = _parse_api(status, text, "auth.getAuthCode")
    session.auth_url = str(payload.get("auth_url") or "")
    session.auth_hash = str(payload.get("auth_hash") or "")
    session.deadline = _compute_deadline(payload.get("expires_in"))
    if not session.auth_url or not session.auth_hash:
        raise VkQrError("VK не вернул auth_url/auth_hash", raw=payload)
    return session


def check_auth_code(client: VkClient, session: Session) -> dict[str, Any]:
    """auth.checkAuthCode — один опрос статуса подтверждения входа."""
    data = {
        "auth_hash": session.auth_hash,
        "web_auth": "1",
        "anonymous_token": session.anonymous_token,
        "access_token": "",
    }
    status, text = client.post(
        f"{API_HOST}/method/auth.checkAuthCode",
        params={"v": V_AUTH_METHODS, "client_id": session.host_app_id},
        data=data,
        headers=_api_headers(),
    )
    payload = _parse_api(status, text, "auth.checkAuthCode")
    return {
        "status": int(payload.get("status") or 0),
        "expires_in": int(payload.get("expires_in") or 0),
        "super_app_token": payload.get("super_app_token") or payload.get("access_token"),
        "raw": payload,
    }


def validate_auth_code(client: VkClient, session: Session, code: str) -> None:
    """auth.validateAuthCode — подтверждение кода, который показал телефон после скана."""
    data = {
        "auth_hash": session.auth_hash,
        "validation_code": code.strip(),
        "access_token": session.anonymous_token,
    }
    status, text = client.post(
        f"{API_HOST}/method/auth.validateAuthCode",
        params={"v": V_AUTH_METHODS, "client_id": session.host_app_id},
        data=data,
        headers=_api_headers(),
    )
    _parse_api(status, text, "auth.validateAuthCode")


def complete_login(client: VkClient, session: Session, super_app_token: str) -> dict[str, Any]:
    """Completes connect_code_auth, following next_step_url when VK returns one."""
    data = {
        "token": super_app_token,
        "uuid": session.uuid,
        "app_id": str(session.host_app_id),
        "flow_start_state": "",
        "is_external_carousel": "",
        "oauth_version": "",
        "sid": "",
        "oauth_force_hash": "0",
        "is_registration": "0",
        "oauth_response_type": "silent_token",
        "vkid_oauth_hash": "",
        "is_oauth_migrated_flow": "0",
        "oauth_state": "",
        "to": base64.b64encode(f"{WEB_HOST}/".encode()).decode(),
        "version": "1",
    }
    status, text = client.post(
        f"{LOGIN_HOST}/?act=connect_code_auth",
        data=data,
        headers={**_api_headers(), "Accept": "application/json, text/plain, */*"},
    )
    try:
        payload: Any = json.loads(text)
    except ValueError as exc:
        raise VkQrError(
            f"connect_code_auth: некорректный ответ (HTTP {status})",
            raw=text[:500],
        ) from exc
    if not isinstance(payload, dict):
        raise VkQrError("connect_code_auth: неожиданный ответ", raw=payload)
    if payload.get("type") == "error" or payload.get("error_code"):
        raise VkQrError(
            str(payload.get("error_info") or payload.get("error_msg") or "Ошибка connect_code_auth"),
            error_code=payload.get("error_code"),
            raw=payload,
        )
    connect_data = payload.get("data") or {}
    if not isinstance(connect_data, dict):
        connect_data = {}
    next_step_url = connect_data.get("next_step_url")
    if not next_step_url:
        if payload.get("type") == "okay":
            return payload
        response_type = payload.get("type")
        data = payload.get("data")
        details = [f"поля ответа: {', '.join(sorted(map(str, payload)))}"]
        if response_type is not None:
            details.append(f"type={str(response_type)[:40]}")
        if isinstance(data, dict):
            details.append(f"поля data: {', '.join(sorted(map(str, data)))}")
        else:
            details.append(f"data имеет тип {type(data).__name__}")
        raise VkQrError(
            f"connect_code_auth не вернул next_step_url ({'; '.join(details)})",
            raw=payload,
        )
    client.get(_absolute_url(str(next_step_url)))
    return payload


def get_user_id(client: VkClient, access_token: str) -> int:
    """Gets the authenticated user's ID from the VK API."""
    if not access_token:
        raise VkQrError("connect_code_auth не вернул access_token для users.get")
    status, text = client.post(
        f"{API_HOST}/method/users.get",
        params={"v": V_USERS_GET},
        data={"access_token": access_token},
        headers=_api_headers(),
    )
    users = _parse_api(status, text, "users.get")
    if not isinstance(users, list) or not users or not isinstance(users[0], dict):
        raise VkQrError("users.get не вернул данные пользователя", raw=users)
    try:
        user_id = int(users[0]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VkQrError("users.get не вернул корректный user_id", raw=users[0]) from exc
    if user_id <= 0:
        raise VkQrError("users.get вернул некорректный user_id", raw=users[0])
    return user_id


def _read_line(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        return input()
    except EOFError:
        raise VkQrError("Не удалось прочитать данные из stdin (поток завершён)") from None


def _enter_code(client: VkClient, session: Session) -> None:
    last_error: Optional[VkQrError] = None
    for _ in range(MAX_CODE_ATTEMPTS):
        try:
            code = _read_line("Введите код с экрана телефона: ").strip()
        except VkQrError:
            raise
        if not code:
            continue
        try:
            validate_auth_code(client, session, code)
            return
        except VkQrError as exc:
            last_error = exc
            print("Неверный код подтверждения, попробуйте ещё раз", file=sys.stderr)
    raise last_error or VkQrError("Не удалось подтвердить код с телефона")


def wait_for_approval(client: VkClient, session: Session, poll_interval: float = DEFAULT_POLL_INTERVAL) -> str:
    """Поллит auth.checkAuthCode до подтверждения входа. Возвращает super_app_token."""
    while True:
        result = check_auth_code(client, session)
        status = result["status"]
        if status == STATUS_APPROVED:
            token = result["super_app_token"]
            if not token:
                raise VkQrError("Статус 2 без super_app_token", raw=result["raw"])
            return str(token)
        if status == STATUS_DECLINED:
            raise VkQrError("Вход отклонён в приложении")
        if status == STATUS_EXPIRED:
            raise VkQrError("Срок действия QR-кода истёк")
        if status == STATUS_AWAIT_CODE:
            _enter_code(client, session)
            continue
        if session.deadline and time.time() > session.deadline:
            raise VkQrError("Срок действия QR-кода истёк")
        time.sleep(poll_interval)


def build_entry(cookies: dict[str, str], user_id: int, now: Optional[float] = None) -> dict[str, Any]:
    missing = [name for name in ("p", "remixsid") if not cookies.get(name)]
    if missing:
        raise VkQrError(f"В cookies отсутствуют обязательные значения: {', '.join(missing)}")
    if not isinstance(user_id, int) or user_id <= 0:
        raise VkQrError("Для сохранения cookies требуется корректный user_id")
    entry = {
        "created_at": int(now if now is not None else time.time()),
        "p": cookies["p"],
        "remixsid": cookies["remixsid"],
        "user_id": user_id,
    }
    return entry


def load_store(path: Any) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {"cookies": []}
    text = file_path.read_text(encoding="utf-8").strip()
    if not text:
        return {"cookies": []}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise StoreError(f"файл {file_path} содержит некорректный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StoreError(f"файл {file_path}: ожидался JSON-объект верхнего уровня")
    cookies = data.get("cookies")
    if cookies is None:
        cookies = []
        data["cookies"] = cookies
    if not isinstance(cookies, list):
        raise StoreError(f'файл {file_path}: поле "cookies" должно быть списком')
    return data


def append_entry(path: Any, entry: dict[str, Any]) -> Path:
    file_path = Path(path)
    data = load_store(file_path)
    data["cookies"].append(entry)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(file_path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, file_path)
    return file_path


def run(
    output: Optional[str],
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = 30.0,
) -> dict[str, Any]:
    client = VkClient(timeout=timeout)
    session = init_session(client)
    get_qr_auth_data(client, session)
    get_qr_code(client, session)

    print(session.auth_url, flush=True)
    print("Отсканируйте QR-код приложением ВКонтакте; не открывайте ссылку в браузере.", file=sys.stderr)
    try:
        import qrcode
    except ImportError:
        print("Не удалось вывести QR-код. Установите пакет qrcode или используйте qrencode.", file=sys.stderr)
    else:
        qr = qrcode.QRCode()
        qr.add_data(session.auth_url)
        qr.make(fit=True)
        qr.print_ascii(out=sys.stderr, tty=sys.stderr.isatty(), invert=True)

    super_app_token = wait_for_approval(client, session, poll_interval=poll_interval)
    login_result = complete_login(client, session, super_app_token)
    login_data = login_result.get("data")
    access_token = login_data.get("access_token") if isinstance(login_data, dict) else None
    user_id = get_user_id(client, str(access_token or ""))
    client.get(f"{WEB_HOST}/feed")

    cookies = client.cookies()
    # print('\n'.join(f'{k}: {v}' for k, v in cookies.items()), flush=True)
    entry = build_entry(cookies, user_id)

    target = output
    if target is None:
        target = _read_line("Укажите путь к JSON-файлу для cookies: ").strip()
        if not target:
            raise VkQrError("Путь к файлу не задан, cookies не сохранены")

    saved = append_entry(target, entry)
    print(f"OK: user_id={user_id}, cookies сохранены в {saved.resolve()}", file=sys.stderr)
    return entry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vkqr",
        description="Получение cookies ВКонтакте по QR-коду (VK ID).",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="JSON-файл для cookies. Если не задан, путь будет запрошен после входа.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help=argparse.SUPPRESS,
    )
    return parser


def _setup_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_streams()
    try:
        run(args.output, poll_interval=args.poll_interval, timeout=args.timeout)
    except VkQrError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано пользователем", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
