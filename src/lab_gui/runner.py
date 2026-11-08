"""Run one command as a child process and stream its output to the GUI.

A reader thread puts output lines on a queue and the Tk loop drains it with `drain()`, so the
window never blocks on the child. The child runs from the repo root with UTF-8 output and, on
Windows, without a console window of its own."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time

from runinfo import ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Run:
    def __init__(self, command: list[str], cwd=ROOT):
        self.command = command
        self.returncode: int | None = None
        self.stopped = False
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._started = time.monotonic()
        self._ended: float | None = None
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        with self.proc.stdout:
            for line in self.proc.stdout:
                self._lines.put(ANSI.sub("", line))
        self.returncode = self.proc.wait()
        self._ended = time.monotonic()
        self._lines.put(None)

    @property
    def done(self) -> bool:
        return self._ended is not None

    @property
    def elapsed(self) -> float:
        return (self._ended or time.monotonic()) - self._started

    def drain(self, limit: int = 2000) -> list[str]:
        """Output lines that arrived since the last call (at most `limit`)."""
        out: list[str] = []
        while len(out) < limit:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                out.append(line)
        return out

    def stop(self, grace: float = 3.0) -> None:
        if self.proc.poll() is not None:
            return
        self.stopped = True
        self.proc.terminate()
        try:
            self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def wait(self, timeout: float | None = None) -> int | None:
        self._reader.join(timeout)
        return self.returncode
