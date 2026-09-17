import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from xjtu_jwc_notices.session import ChallengeProtocolError, XjtuSiteSession, simple_hash

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_simple_hash_matches_published_signed_32_bit_absolute_algorithm() -> None:
    assert simple_hash("notice-list-20260917202Mozilla/5.") == 764368191


def test_get_solves_dynamic_challenge_then_retries_once(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    target = "https://jwc.xjtu.edu.cn/jxtz2.htm"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        list_requests = [item for item in calls if item.url.path == "/jxtz2.htm"]
        if request.url.path == "/jxtz2.htm" and len(list_requests) == 1:
            return httpx.Response(200, text=fixture("dynamic_challenge.html"), request=request)
        if request.url.path == "/dynamic_challenge":
            assert request.method == "POST"
            assert request.headers["content-type"] == "application/json"
            assert json.loads(request.content) == {
                "challenge_id": "notice-list-20260917",
                "answer": 202,
                "browser_info": {
                    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "language": "zh-CN",
                    "platform": "Win32",
                    "screen": {"width": 1920, "height": 1080, "colorDepth": 24},
                    "timezoneOffset": -480,
                    "hasTouchEvents": False,
                },
                "hash": 764368191,
            }
            return httpx.Response(200, json={"client_id": "client-abc"}, request=request)
        assert request.headers["cookie"] == "client_id=client-abc"
        return httpx.Response(200, text="<html>notice list</html>", request=request)

    session = XjtuSiteSession(
        cookie_file=tmp_path / "cookies.json",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=lambda _: None,
        backoff=lambda _: None,
    )
    try:
        response = session.get(target)
    finally:
        session.close()

    assert response.text == "<html>notice list</html>"
    assert [request.url.path for request in calls] == [
        "/jxtz2.htm",
        "/dynamic_challenge",
        "/jxtz2.htm",
    ]


def test_get_rejects_changed_challenge_structure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='<script>var challenge_id = "x";</script>', request=request
        )

    session = XjtuSiteSession(
        cookie_file=tmp_path / "cookies.json",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=lambda _: None,
        backoff=lambda _: None,
    )
    try:
        with pytest.raises(ChallengeProtocolError, match="structure"):
            session.get("https://jwc.xjtu.edu.cn/jxtz2.htm")
    finally:
        session.close()


def test_get_retries_transient_errors_with_numeric_retry_after(tmp_path: Path) -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    session = XjtuSiteSession(
        cookie_file=tmp_path / "cookies.json",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=lambda _: None,
        backoff=waits.append,
    )
    try:
        assert session.get("https://jwc.xjtu.edu.cn/jxtz2.htm").text == "ok"
    finally:
        session.close()

    assert calls == 2
    assert waits == [7.0]


def test_get_honors_http_date_retry_after(tmp_path: Path) -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},
                request=request,
            )
        return httpx.Response(200, text="ok", request=request)

    session = XjtuSiteSession(
        cookie_file=tmp_path / "cookies.json",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=lambda _: None,
        backoff=waits.append,
    )
    try:
        assert session.get("https://jwc.xjtu.edu.cn/jxtz2.htm").text == "ok"
    finally:
        session.close()

    assert waits == [0.0]


def test_cookie_file_rejects_non_string_cookie_fields(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        json.dumps([{"name": 123, "value": "x", "domain": "jwc.xjtu.edu.cn", "path": "/"}]),
        encoding="utf-8",
    )

    with pytest.raises(ChallengeProtocolError, match="cookie"):
        XjtuSiteSession(
            cookie_file=cookie_file,
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
        )


def test_cookie_file_is_replaced_atomically(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.json"
    session = XjtuSiteSession(
        cookie_file=cookie_file,
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    session.client.cookies.set("client_id", "client-abc", domain="jwc.xjtu.edu.cn", path="/")

    original_replace = Path.replace
    with patch.object(Path, "replace", autospec=True, side_effect=original_replace) as replace:
        session.close()

    assert replace.call_count == 1
    assert json.loads(cookie_file.read_text(encoding="utf-8"))[0]["value"] == "client-abc"
