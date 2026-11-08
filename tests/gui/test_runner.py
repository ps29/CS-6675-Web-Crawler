import sys

from lab_gui.registry import TASKS, python_exe
from lab_gui.runner import Run


def test_run_streams_output_and_exit_code():
    run = Run([sys.executable, "-c", "print('hi'); print('\x1b[32mgreen\x1b[0m')"])
    assert run.wait(30) == 0
    assert run.drain() == ["hi\n", "green\n"]
    assert run.done and not run.stopped


def test_nonzero_exit_code_is_kept():
    run = Run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert run.wait(30) == 3


def test_stop_ends_a_long_run():
    run = Run([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"])
    run.stop(grace=1)
    run.wait(10)
    assert run.done and run.stopped and run.elapsed < 30


def test_commands_use_the_console_interpreter():
    command = TASKS[0].command(["crawl"])
    assert command[:4] == [python_exe(), "-u", "-m", "focused_crawler.cli"]
    assert not command[0].lower().endswith("pythonw.exe")
