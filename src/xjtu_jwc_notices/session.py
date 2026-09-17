"""Sequential, low-rate HTTP session for the XJTU teaching-notice site."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlparse

import httpx


class ChallengeProtocolError(RuntimeError):
    """Raised when the site's published challenge protocol changes."""


_CHALLENGE_VARIABLES = {
    name: re.compile(rf"\bvar\s+{name}\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|(-?\d+))\s*;")
    for name in ("challenge_id", "a", "b", "operator")
}
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_BROWSER_INFO = {
    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "language": "zh-CN",
    "platform": "Win32",
    "screen": {"width": 1920, "height": 1080, "colorDepth": 24},
    "timezoneOffset": -480,
    "hasTouchEvents": False,
}


def simple_hash(value: str) -> int:
    """Run the challenge page's published JavaScript ``simpleHash`` routine."""

    hashed = 0
    for character in value:
        hashed = ((hashed << 5) - hashed + ord(character)) & 0xFFFFFFFF
    if hashed >= 0x80000000:
        hashed -= 0x100000000
    return abs(hashed)


def _challenge_result(a: int, b: int, operator: str) -> int:
    operations: dict[str, Callable[[int, int], int]] = {
        "+": lambda left, right: left + right,
        "-": lambda left, right: left - right,
        "*": lambda left, right: left * right,
    }
    if operator == "/":
        if b == 0 or a % b:
            raise ChallengeProtocolError("challenge division is not an exact integer")
        return a // b
    elif operation := operations.get(operator):
        return operation(a, b)
    raise ChallengeProtocolError(f"unsupported challenge operator {operator!r}")


class XjtuSiteSession:
    """A synchronous XJTU site client with persistent cookies and challenge handling."""

    def __init__(
        self,
        *,
        cookie_file: Path,
        client: httpx.Client | None = None,
        request_delay_seconds: float = 1.0,
        delay: Callable[[float], None] = time.sleep,
        backoff: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        browser_info: dict[str, object] | None = None,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must be non-negative")
        self.cookie_file = cookie_file
        self.client = client or httpx.Client(follow_redirects=True, timeout=30.0)
        self.request_delay_seconds = request_delay_seconds
        self.delay = delay
        self.backoff = backoff
        self.max_retries = max_retries
        self.browser_info = browser_info or DEFAULT_BROWSER_INFO
        self._load_cookies()

    def _load_cookies(self) -> None:
        if not self.cookie_file.exists():
            return
        try:
            cookies = json.loads(self.cookie_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ChallengeProtocolError(f"could not load persisted cookies: {error}") from error
        if not isinstance(cookies, list):
            raise ChallengeProtocolError("persisted cookie file has changed structure")
        for item in cookies:
            required_keys = ("name", "value", "domain", "path")
            if not isinstance(item, dict) or not all(key in item for key in required_keys):
                raise ChallengeProtocolError("persisted cookie file has changed structure")
            if not all(isinstance(item[key], str) for key in required_keys):
                raise ChallengeProtocolError("persisted cookie file has invalid values")
            if not item["name"] or not item["domain"] or not item["path"].startswith("/"):
                raise ChallengeProtocolError("persisted cookie file has invalid values")
            self.client.cookies.set(
                item["name"], item["value"], domain=item["domain"], path=item["path"]
            )

    def _save_cookies(self) -> None:
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookies = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
            for cookie in self.client.cookies.jar
        ]
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.cookie_file.parent,
            prefix=f".{self.cookie_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(json.dumps(cookies, ensure_ascii=False, indent=2))
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(self.cookie_file)

    def close(self) -> None:
        self._save_cookies()
        self.client.close()

    def __enter__(self) -> XjtuSiteSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(retry_after)
                    except (TypeError, ValueError):
                        retry_at = None
                    if retry_at is not None:
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=UTC)
                        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
        return float(2**attempt)

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            self.delay(self.request_delay_seconds)
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                self.backoff(self._retry_delay(None, attempt))
                continue
            if response.status_code not in _TRANSIENT_STATUS_CODES or attempt == self.max_retries:
                return response
            self.backoff(self._retry_delay(response, attempt))
        raise AssertionError("unreachable retry loop")

    @staticmethod
    def _challenge_values(html: str) -> tuple[str, int, int, str] | None:
        if "challenge_id" not in html:
            return None
        values: dict[str, str] = {}
        for name, pattern in _CHALLENGE_VARIABLES.items():
            match = pattern.search(html)
            if not match:
                raise ChallengeProtocolError("dynamic challenge structure has changed")
            values[name] = next(value for value in match.groups() if value is not None)
        try:
            return values["challenge_id"], int(values["a"]), int(values["b"]), values["operator"]
        except ValueError as error:
            raise ChallengeProtocolError("dynamic challenge values have changed") from error

    def _solve_challenge(self, original_url: str, values: tuple[str, int, int, str]) -> None:
        challenge_id, a, b, operator = values
        answer = _challenge_result(a, b, operator)
        user_agent = self.browser_info.get("userAgent")
        if not isinstance(user_agent, str):
            raise ChallengeProtocolError("browser_info must contain a string userAgent")
        payload = {
            "challenge_id": challenge_id,
            "answer": answer,
            "browser_info": self.browser_info,
            "hash": simple_hash(f"{challenge_id}{answer}{user_agent[:10]}"),
        }
        response = self._request(
            "POST",
            urljoin(original_url, "/dynamic_challenge"),
            json=payload,
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ChallengeProtocolError("dynamic challenge response is not JSON") from error
        client_id = payload.get("client_id") if isinstance(payload, dict) else None
        if not isinstance(client_id, str) or not client_id:
            raise ChallengeProtocolError(
                "dynamic challenge response has changed: missing client_id"
            )
        host = urlparse(original_url).hostname
        if not host:
            raise ChallengeProtocolError("original URL has no host")
        self.client.cookies.set("client_id", client_id, domain=host, path="/")
        self._save_cookies()

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        """GET one page, solving at most one dynamic challenge before retrying once."""

        response = self._request("GET", url, **kwargs)
        challenge = self._challenge_values(response.text)
        if challenge is None:
            return response
        self._solve_challenge(url, challenge)
        retry = self._request("GET", url, **kwargs)
        if self._challenge_values(retry.text) is not None:
            raise ChallengeProtocolError("dynamic challenge remained after one retry")
        return retry
