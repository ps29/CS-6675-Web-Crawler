"""The experiments and checks the GUI can run, grouped by milestone.

Each task's form comes from the program's own `build_parser()`, so the GUI shows exactly the
options the CLI accepts. pytest and ruff get small stand-in parsers for the options worth a
form field; anything else can be typed into a terminal from the copied command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

from lab_gui.forms import Form, build_form


def python_exe() -> str:
    """The console interpreter, even when the GUI itself runs under pythonw.exe."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console) if exe.name.lower() == "pythonw.exe" and console.exists() else str(exe)


def _never(values: dict) -> bool:
    return False


def _always(values: dict) -> bool:
    return True


@dataclass(frozen=True)
class Task:
    group: str
    title: str
    module: str
    parser: Callable[[], argparse.ArgumentParser] | None = None  # default: module.build_parser
    subcommand: str | None = None
    prefix: tuple[str, ...] = ()  # argv placed before the form's, e.g. ruff's "check"
    preset: dict = field(default_factory=dict)  # form values that differ from the parser's
    online: Callable[[dict], bool] = _never  # whether these values make it use the network

    def form(self) -> Form:
        parser = self.parser() if self.parser else import_module(self.module).build_parser()
        return build_form(parser, self.subcommand)

    def initial(self, form: Form) -> dict:
        return {**form.initial(), **self.preset}

    def command(self, argv: list[str]) -> list[str]:
        return [python_exe(), "-u", "-m", self.module, *self.prefix, *argv]


def pytest_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pytest", description="Run the offline test suite.")
    ap.add_argument("paths", nargs="*", default=["tests"], help="test files, folders or node IDs")
    ap.add_argument("-k", dest="keyword", help="only tests matching this expression")
    ap.add_argument("-x", "--exitfirst", action="store_true", help="stop at the first failure")
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per test")
    ap.add_argument("--lf", dest="last_failed", action="store_true", help="rerun last failures")
    ap.add_argument("--cov", action="store_true", help="coverage report (pytest-cov)")
    return ap


def ruff_parser(description: str, *flags: tuple[str, str]) -> Callable[[], argparse.ArgumentParser]:
    def build() -> argparse.ArgumentParser:
        ap = argparse.ArgumentParser(prog="ruff", description=description)
        ap.add_argument("paths", nargs="*", default=["."], help="files or folders")
        for flag, help_ in flags:
            ap.add_argument(flag, action="store_true", help=help_)
        return ap

    return build


M1, LEDGER, EDGE, CHECKS = (
    "M1 focused crawler", "M2/M3 crawl ledger", "M4 edge serving", "Tests and lint"
)  # fmt: skip
FCRAWL = "focused_crawler.cli"

TASKS: tuple[Task, ...] = (
    Task(M1, "Crawl (fcrawl crawl)", FCRAWL, subcommand="crawl", online=_always),
    Task(M1, "Search (fcrawl search)", FCRAWL, subcommand="search", preset={"query": "json.dumps"}),
    Task(M1, "Stats (fcrawl stats)", FCRAWL, subcommand="stats"),
    Task(M1, "Export (fcrawl export)", FCRAWL, subcommand="export"),
    Task(M1, "Ranking evaluation", "focused_crawler.evaluate"),
    Task(LEDGER, "Ledger sim (offline)", "crawl_ledger.sim", preset={"offline": True},
         online=lambda v: not v.get("offline")),
    Task(LEDGER, "Ledger sim (live PyPI)", "crawl_ledger.sim",
         online=lambda v: not v.get("offline")),
    Task(LEDGER, "M3 refinements (sim_m3)", "crawl_ledger.sim_m3",
         online=lambda v: bool(v.get("live"))),
    Task(LEDGER, "Host-rate sim (rate_sim)", "crawl_ledger.rate_sim"),
    Task(LEDGER, "Audit eval: collect", "crawl_ledger.audit_eval", subcommand="collect",
         online=_always),
    Task(LEDGER, "Audit eval: evaluate", "crawl_ledger.audit_eval", subcommand="evaluate"),
    Task(EDGE, "Edge sim", "edge_serving.sim"),
    Task(EDGE, "Edge sim (compare all)", "edge_serving.sim", preset={"compare": True}),
    Task(EDGE, "HTTP demo (aiohttp)", "edge_serving.http_app", preset={"command": "demo"}),
    Task(CHECKS, "pytest: all", "pytest", pytest_parser),
    Task(CHECKS, "pytest: M2/M3 ledger", "pytest", pytest_parser,
         preset={"paths": "tests/ledger"}),
    Task(CHECKS, "pytest: M4 edge", "pytest", pytest_parser, preset={"paths": "tests/edge"}),
    Task(CHECKS, "pytest: GUI", "pytest", pytest_parser, preset={"paths": "tests/gui"}),
    Task(CHECKS, "ruff check", "ruff", ruff_parser("Lint.", ("--fix", "apply safe fixes")),
         prefix=("check",)),
    Task(CHECKS, "ruff format", "ruff",
         ruff_parser("Format.", ("--check", "only report, change nothing"),
                     ("--diff", "show the changes, change nothing")),
         prefix=("format",), preset={"check": True}),
)  # fmt: skip


def groups() -> dict[str, list[Task]]:
    out: dict[str, list[Task]] = {}
    for task in TASKS:
        out.setdefault(task.group, []).append(task)
    return out
