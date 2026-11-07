from focused_crawler.fetcher import Fetcher
from tests.fakes import FakeClock, FakeResponse, FakeSession

ROBOTS = "User-agent: *\nDisallow: /private/\n"


def make_fetcher(routes, delay=1.0):
    clock = FakeClock()
    session = FakeSession(routes)
    fetcher = Fetcher(delay=delay, session=session, sleep=clock.sleep, clock=clock)
    return fetcher, session, clock


def test_respects_robots_txt():
    fetcher, session, _ = make_fetcher(
        {
            "https://a.gatech.edu/robots.txt": FakeResponse(text=ROBOTS),
            "https://a.gatech.edu/ok": "<html>ok</html>",
        }
    )
    assert fetcher.fetch("https://a.gatech.edu/private/x") is None
    result = fetcher.fetch("https://a.gatech.edu/ok")
    assert result.ok_html and "ok" in result.text
    assert "https://a.gatech.edu/private/x" not in session.calls
    assert session.calls.count("https://a.gatech.edu/robots.txt") == 1


def test_sets_user_agent():
    fetcher, session, _ = make_fetcher({})
    assert "focused-crawler" in session.headers["User-Agent"]


def test_waits_between_requests_to_same_host():
    fetcher, _, clock = make_fetcher({"https://a.edu/1": "x", "https://a.edu/2": "y"})
    fetcher.fetch("https://a.edu/1")
    fetcher.fetch("https://a.edu/2")
    assert len(clock.slept) == 2  # robots->page1, page1->page2
    assert all(abs(s - 1.0) < 1e-9 for s in clock.slept)


def test_retries_on_server_error():
    class Flaky(FakeSession):
        def get(self, url, timeout=0, allow_redirects=True):
            self.calls.append(url)
            if url.endswith("robots.txt"):
                return FakeResponse(status_code=404, url=url)
            status = 503 if self.calls.count(url) == 1 else 200
            return FakeResponse(status_code=status, text="fine", url=url)

    clock = FakeClock()
    fetcher = Fetcher(session=Flaky(), sleep=clock.sleep, clock=clock, max_retries=2)
    assert fetcher.fetch("https://a.edu/p").status == 200


def test_non_html_has_empty_text():
    fetcher, _, _ = make_fetcher(
        {"https://a.edu/f": FakeResponse(text="%PDF", headers={"Content-Type": "application/pdf"})}
    )
    result = fetcher.fetch("https://a.edu/f")
    assert result.text == "" and not result.ok_html


def test_parses_last_modified():
    fetcher, _, _ = make_fetcher(
        {
            "https://a.edu/p": FakeResponse(
                text="x",
                headers={
                    "Content-Type": "text/html",
                    "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT",
                },
            )
        }
    )
    assert fetcher.fetch("https://a.edu/p").last_modified == 1445412480.0


def test_decodes_utf8_when_charset_missing():
    page = "<title>json — JSON encoder</title>"
    resp = FakeResponse(text=page.encode("utf-8").decode("latin-1"))  # what requests guesses
    resp.headers = {"Content-Type": "text/html"}
    resp.content = page.encode("utf-8")
    fetcher, _, _ = make_fetcher({"https://a.edu/p": resp})
    assert fetcher.fetch("https://a.edu/p").text == page


def test_declared_charset_is_trusted():
    resp = FakeResponse(text="café", headers={"Content-Type": "text/html; charset=latin-1"})
    resp.content = "café".encode("latin-1")
    fetcher, _, _ = make_fetcher({"https://a.edu/p": resp})
    assert fetcher.fetch("https://a.edu/p").text == "café"
