import argparse

import pytest

from crawl_ledger import audit_eval, sim, sim_m3
from edge_serving import http_app
from focused_crawler import evaluate
from lab_gui.forms import build_form, to_argv
from lab_gui.registry import TASKS


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.title)
def test_preset_values_are_valid_and_parse(task):
    form = task.form()
    argv, errors = to_argv(form, task.initial(form))
    assert errors == {}
    if task.parser is None:  # a project program: its own parser must accept the argv
        from importlib import import_module

        import_module(task.module).build_parser().parse_args([*task.prefix, *argv])


def test_parser_defaults_give_no_options():
    form = build_form(sim.build_parser())
    assert to_argv(form, form.initial()) == ([], {})


def test_edited_values_become_flags():
    form = build_form(sim_m3.build_parser())
    values = {**form.initial(), "peers": "40", "collude": True, "variant": "r1", "live": True}
    argv, errors = to_argv(form, values)
    assert errors == {}
    assert argv == ["--variant", "r1", "--peers", "40", "--no-collude", "--live"]
    a = sim_m3.build_parser().parse_args(argv)
    assert (a.peers, a.collude, a.variant, a.live) == (40, False, "r1", True)


def test_value_equal_to_default_is_left_out():
    form = build_form(sim.build_parser())
    argv, _ = to_argv(form, {**form.initial(), "dishonest": "0.20", "peers": " 20 "})
    assert argv == []


def test_bad_values_are_reported_not_raised():
    form = build_form(sim.build_parser())
    argv, errors = to_argv(form, {**form.initial(), "peers": "many", "seed": "2"})
    assert set(errors) == {"peers"} and "int" in errors["peers"]
    assert argv == ["--seed", "2"]


def test_list_fields_split_and_check_choices():
    from crawl_ledger import rate_sim

    form = build_form(rate_sim.build_parser())
    argv, errors = to_argv(form, {**form.initial(), "peers": "5 10", "modes": "nope"})
    assert argv == ["--peers", "5", "10"]
    assert "choose from" in errors["modes"]


def test_subcommand_options_follow_it():
    from focused_crawler import cli

    form = build_form(cli.build_parser(), "search")
    values = {**form.initial(), "db": "data/x.db", "query": "json dumps", "ranker": "bm25f"}
    argv, errors = to_argv(form, values)
    assert errors == {}
    assert argv == ["--db", "data/x.db", "search", "json dumps", "--ranker", "bm25f"]
    assert form.description == "query the index"


def test_missing_positional_is_an_error():
    form = build_form(http_app.build_parser())
    assert to_argv(form, form.initial())[1] == {"command": "required"}


@pytest.mark.parametrize(
    "module, argv",
    [
        (sim, ["--peers", "8", "--offline"]),
        (sim_m3, ["--compare", "--no-collude"]),
        (audit_eval, ["evaluate", "--budget", "5"]),
        (evaluate, ["--out", "x.json"]),
        (http_app, ["demo", "--port", "9000"]),
    ],
)
def test_extracted_build_parser_parses_like_before(module, argv):
    assert isinstance(module.build_parser().parse_args(argv), argparse.Namespace)
