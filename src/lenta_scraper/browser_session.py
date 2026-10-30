"""Импорт сессии живого браузера из 'Copy as cURL' в DevTools.

Зачем: Qrator/Лента пускают браузер, но валят headless-Chromium и httpx.
Если просто перетянуть готовые cookies+headers — скрапер ходит как браузер.

Файл сессии: recon/browser_session.json (создаётся `lenta-scrape import-session`).
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .config import RECON_DIR

SESSION_FILE = RECON_DIR / "browser_session.json"
ENDPOINTS_FILE = RECON_DIR / "endpoints.json"

# Заголовки, которые НЕ должен таскать httpx (служебные / HTTP/2 pseudo / противоречат httpx).
_SKIP_HEADERS = frozenset(
    {
        "host", "content-length", "connection", "accept-encoding",
        "transfer-encoding", "expect", "upgrade", "te",
    }
)


@dataclass
class BrowserSession:
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    def save(self, path: Path = SESSION_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"cookies": self.cookies, "headers": self.headers},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path = SESSION_FILE) -> "BrowserSession | None":
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(cookies=data.get("cookies", {}), headers=data.get("headers", {}))


def _parse_cookie_header(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


@dataclass
class ParsedCurl:
    method: str
    url: str
    headers: dict[str, str]
    cookies: dict[str, str]
    body: str | None


def parse_curl(curl_text: str) -> ParsedCurl:
    """Распарсить вывод 'Copy as cURL (bash)' из DevTools."""
    text = curl_text.replace("\\\n", " ").strip()
    tokens = shlex.split(text)
    if not tokens or "curl" not in tokens[0]:
        raise ValueError(
            "Это не похоже на 'curl ...'. Скопируй из DevTools → Copy → Copy as cURL (bash)."
        )

    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    body: str | None = None
    method: str | None = None
    url: str | None = None
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-H", "--header") and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ":" in raw:
                name, _, val = raw.partition(":")
                name = name.strip()
                val = val.strip()
                if name.startswith(":"):
                    pass
                elif name.lower() == "cookie":
                    cookies.update(_parse_cookie_header(val))
                elif name.lower() not in _SKIP_HEADERS:
                    headers[name] = val
            i += 2
        elif t in ("-b", "--cookie") and i + 1 < len(tokens):
            cookies.update(_parse_cookie_header(tokens[i + 1]))
            i += 2
        elif t in ("-A", "--user-agent") and i + 1 < len(tokens):
            headers["User-Agent"] = tokens[i + 1]
            i += 2
        elif t in ("-e", "--referer") and i + 1 < len(tokens):
            headers["Referer"] = tokens[i + 1]
            i += 2
        elif t in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(
            tokens
        ):
            body = tokens[i + 1]
            i += 2
        elif t in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1]
            i += 2
        elif t.startswith("http://") or t.startswith("https://"):
            url = t
            i += 1
        else:
            i += 1

    if url is None:
        raise ValueError("Не нашёл URL в cURL.")
    if method is None:
        method = "POST" if body is not None else "GET"

    return ParsedCurl(
        method=method.upper(),
        url=url,
        headers=headers,
        cookies=cookies,
        body=body,
    )


def save_browser_session(parsed: ParsedCurl) -> BrowserSession:
    if not parsed.cookies:
        raise ValueError(
            "В cURL нет cookies. Открой /catalog/ в браузере и скопируй ИМЕННО ТОТ запрос."
        )
    sess = BrowserSession(cookies=parsed.cookies, headers=parsed.headers)
    sess.save()
    return sess


def save_api_endpoint(parsed: ParsedCurl) -> tuple[str, dict]:
    """Записать XHR-запрос к /api-gateway/* как шаблон в endpoints.json."""
    from urllib.parse import urlparse

    path = urlparse(parsed.url).path
    key = f"{parsed.method} {path}"
    entry = {
        "method": parsed.method,
        "url": parsed.url,
        "request_headers": parsed.headers,
        "request_body": parsed.body,
    }
    existing: dict = {}
    if ENDPOINTS_FILE.exists():
        try:
            existing = json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing[key] = entry
    ENDPOINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENDPOINTS_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return key, entry


def is_api_endpoint(parsed: ParsedCurl) -> bool:
    return "/api-gateway/" in parsed.url or "/api/" in parsed.url
