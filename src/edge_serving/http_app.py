"""asyncio HTTP front ends for the gateway and edges (aiohttp).

Edges coalesce concurrent misses for the same path into one upstream fetch (M4 Section 4.4).

Usage:  python -m edge_serving.http_app demo     # 1 gateway + 3 edges on localhost
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor

import requests
from aiohttp import web

from .client import Client
from .edge import Edge
from .gateway import Gateway
from .ledger_view import build_demo_ledger
from .objects import Response

Handler = Callable[[str], Response]


def to_http(resp: Response) -> web.Response:
    return web.json_response(
        resp.body, status=resp.status, headers={"Cache-Control": resp.cache_control}
    )


def gateway_app(gateway: Gateway) -> web.Application:
    async def handle(request: web.Request) -> web.Response:
        return to_http(gateway.get(request.path))

    app = web.Application()
    app.router.add_get("/{tail:.*}", handle)
    return app


def http_upstream(base_url: str, timeout: float = 10.0) -> Handler:
    """Blocking upstream for an Edge: GET base_url + path, keeping Cache-Control."""
    session = requests.Session()

    def get(path: str) -> Response:
        try:
            r = session.get(base_url.rstrip("/") + path, timeout=timeout)
            body = r.json()
        except (requests.RequestException, ValueError):
            return Response(503, {"error": "gateway unavailable"})
        return Response(r.status_code, body, r.headers.get("Cache-Control", "no-store"))

    return get


class EdgeServer:
    """Serializes cache access on one worker thread; concurrent requests for the same path
    share one in-flight future, so a burst of misses reaches the gateway once."""

    def __init__(self, edge: Edge):
        self.edge = edge
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=edge.name)
        self._inflight: dict[str, asyncio.Future] = {}
        self.coalesced = 0

    async def _run(self, key: str, fn: Callable[[], Response]) -> Response:
        if key in self._inflight:
            self.coalesced += 1
            return await asyncio.shield(self._inflight[key])
        loop = asyncio.get_running_loop()
        fut: Awaitable[Response] = loop.run_in_executor(self._executor, fn)
        self._inflight[key] = asyncio.ensure_future(fut)
        try:
            return await self._inflight[key]
        finally:
            del self._inflight[key]

    async def handle(self, request: web.Request) -> web.Response:
        path = request.path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "latest":
            resp = await self._run(path, lambda: self.edge.get_latest(parts[1], parts[2]))
        elif len(parts) == 2 and parts[0] in ("proof", "status"):
            resp = await self._run(path, lambda: self.edge.get(path))
        else:
            resp = Response(400, {"error": "unknown path"})
        return to_http(resp)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/{tail:.*}", self.handle)
        app.on_cleanup.append(lambda _: asyncio.to_thread(self._executor.shutdown))
        return app


async def _start(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def demo(base_port: int = 8080, n_edges: int = 3) -> dict:
    ledger = build_demo_ledger(["requests", "numpy", "flask"], epochs=2)
    gateway = Gateway(ledger)
    runners = [await _start(gateway_app(gateway), base_port)]
    upstream = http_upstream(f"http://127.0.0.1:{base_port}")
    servers = [EdgeServer(Edge(f"edge{i}", upstream, time.monotonic)) for i in range(n_edges)]
    for i, server in enumerate(servers, 1):
        runners.append(await _start(server.app(), base_port + i))

    url = f"http://127.0.0.1:{base_port + 1}/latest/requests/pypi"
    try:
        bodies = await asyncio.gather(*(asyncio.to_thread(requests.get, url) for _ in range(5)))
        body = bodies[0].json()
        client = Client(ledger.chain, ledger.directory, gateway.public_key)
        return {
            "url": url,
            "verdict": client.check_latest(body),
            "state": body["status"]["state"],
            "gateway_requests": gateway.requests,
            "coalesced": servers[0].coalesced,
            "edge_hits": servers[0].edge.stats.hits,
        }
    finally:
        for runner in runners:
            await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edge_serving.http_app", description=__doc__)
    ap.add_argument("command", choices=("demo",))
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args(argv)
    print(json.dumps(asyncio.run(demo(a.port)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
