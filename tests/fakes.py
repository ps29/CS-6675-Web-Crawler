"""Offline stand-ins for requests.Session used across tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    headers: dict = field(default_factory=lambda: {"Content-Type": "text/html; charset=utf-8"})
    url: str = ""


class FakeSession:
    """Serves canned responses by URL; unknown URLs return 404."""

    def __init__(self, routes: dict[str, FakeResponse | str] | None = None):
        self.headers: dict[str, str] = {}
        self.routes = routes or {}
        self.calls: list[str] = []

    def get(self, url: str, timeout: float = 0, allow_redirects: bool = True) -> FakeResponse:
        self.calls.append(url)
        resp = self.routes.get(url)
        if resp is None:
            return FakeResponse(status_code=404, text="", url=url)
        if isinstance(resp, str):
            resp = FakeResponse(text=resp)
        if not resp.url:
            resp.url = url
        return resp


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
