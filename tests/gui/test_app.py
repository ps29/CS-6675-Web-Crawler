import json

import pytest

tk = pytest.importorskip("tkinter")

from lab_gui.app import App, line_tag, read_result  # noqa: E402


@pytest.fixture(scope="module")
def root():
    # One Tk per process: a second tk.Tk() on Windows intermittently fails to find its Tcl
    # library ("invalid command name tcl_findLibrary").
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(root):
    window = tk.Toplevel(root)
    window.withdraw()
    yield App(window)
    window.destroy()


def test_app_builds_every_task_form(app):
    for iid in app.tasks:
        app.tree.selection_set(iid)
        app._on_select()
        assert app.panel is app.panels[iid]
        assert app.command.get().endswith(app.panel.task.command(app.panel.argv()[0])[-1])


def test_bad_value_disables_run(app):
    iid = next(i for i, t in app.tasks.items() if t.module == "edge_serving.sim")
    app.tree.selection_set(iid)
    app._on_select()
    app.panel.vars["requests"].set("lots")
    assert str(app.run_button["state"]) == "disabled"
    app.panel.vars["requests"].set("600")
    assert str(app.run_button["state"]) == "normal"
    assert "--requests 600" in app.command.get()


def test_pytest_summary_lines_are_coloured():
    assert line_tag("===== 3 passed in 1.20s =====\n", pytest=True) == "pass"
    assert line_tag("===== 1 failed, 2 passed in 1.20s =====\n", pytest=True) == "fail"
    assert line_tag("77 passed in 8.83s\n", pytest=True) == "pass"  # -q, the project default
    assert line_tag("2 passed, 1 error in 0.50s\n", pytest=True) == "fail"
    assert line_tag("3 packages in 2.0s\n") is None  # only pytest runs get summary colours
    assert line_tag("FAILED tests/x.py::test_y - assert 0\n") == "error"
    assert line_tag("ordinary\n") is None


def test_result_preview_puts_provenance_first(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"results": [1], "provenance": {"git_commit": "abc"}}))
    assert read_result(path).index("provenance") < read_result(path).index("results")
