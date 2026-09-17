from pathlib import Path

import httpx
import pytest

from xjtu_jwc_notices.session import ChallengeProtocolError, XjtuSiteSession, simple_hash

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_simple_hash_matches_published_addition_algorithm() -> None:
    assert simple_hash("notice-list-20260917", 173, 29, "+") == "0921c5c5"


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
            assert request.content == b"challenge_id=notice-list-20260917&answer=0921c5c5"
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
