"""CS 6675 lab: a Windows GUI that runs the project's experiments, tests and lint.

Pick a task on the left, edit its options (generated from the program's argparse parser), and
press Run. Output streams into the console; the command line above it can be copied to repeat
the run in a terminal. The Results tab browses `results/`. Start with `lab-gui` or
`python -m lab_gui`."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from lab_gui.forms import Field, Form, to_argv
from lab_gui.registry import Task, groups
from lab_gui.runner import Run
from runinfo import ROOT

RESULTS = ROOT / "results"
POLL_MS = 100
MAX_PREVIEW_CHARS = 400_000
# "===== 3 passed in 1.2s =====", or bare "3 passed in 1.2s" under -q (the project default)
PYTEST_SUMMARY = re.compile(r"^(=+ )?(\d+ \w+|no tests ran)\b.* in [\d.]+s\b")
FAILURE_LINE = re.compile(r"^(FAILED|ERROR) |Traceback \(most recent call last\)")
MONO = ("Consolas", 10)
FIELD_CHARS = 48


class TaskPanel(ttk.Frame):
    """The options form for one task, holding its values while other tasks are shown."""

    def __init__(self, master, task: Task, on_change):
        super().__init__(master)
        self.task = task
        self.form: Form = task.form()
        self.vars: dict[str, tk.Variable] = {}
        self.errors: dict[str, ttk.Label] = {}
        self.on_change = on_change
        self.columnconfigure(3, weight=1)
        for row, field in enumerate(self.form.fields):
            self._add_row(row, field, task.initial(self.form)[field.dest])

    def _add_row(self, row: int, field: Field, value) -> None:
        ttk.Label(self, text=field.label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        if field.kind == "flag":
            var: tk.Variable = tk.BooleanVar(value=value)
            widget = ttk.Checkbutton(self, variable=var)
        elif field.kind == "choice":
            var = tk.StringVar(value=value)
            values = ([""] if field.default is None else []) + [str(c) for c in field.choices]
            widget = ttk.Combobox(
                self, textvariable=var, values=values, state="readonly", width=FIELD_CHARS - 3
            )
        else:
            var = tk.StringVar(value=value)
            widget = ttk.Entry(self, textvariable=var, width=FIELD_CHARS)
        widget.grid(row=row, column=1, sticky="w", pady=2)
        error = ttk.Label(self, foreground="firebrick")
        error.grid(row=row, column=2, sticky="w", padx=(8, 0))
        hint = field.help + (" (space-separated)" if field.kind == "list" else "")
        ttk.Label(self, text=hint, foreground="gray40").grid(row=row, column=3, sticky="w", padx=8)
        self.vars[field.dest] = var
        self.errors[field.dest] = error
        var.trace_add("write", lambda *_: self.on_change())

    def values(self) -> dict:
        return {dest: var.get() for dest, var in self.vars.items()}

    def reset(self) -> None:
        for dest, value in self.task.initial(self.form).items():
            self.vars[dest].set(value)

    def argv(self) -> tuple[list[str], dict[str, str]]:
        argv, errors = to_argv(self.form, self.values())
        for dest, label in self.errors.items():
            label.configure(text=errors.get(dest, ""))
        return argv, errors


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=6)
        self.root = root
        self.panels: dict[str, TaskPanel] = {}
        self.tasks: dict[str, Task] = {}
        self.panel: TaskPanel | None = None
        self.run: Run | None = None
        self.run_task: Task | None = None
        root.title("CS 6675 Lab")
        width = min(1280, root.winfo_screenwidth() - 40)  # fits portrait and small screens
        height = min(820, root.winfo_screenheight() - 80)
        root.geometry(f"{width}x{height}+20+20")
        root.minsize(min(900, width), min(560, height))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.pack(fill="both", expand=True)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panes.add(self._build_tree(panes), weight=0)
        self.tabs = ttk.Notebook(panes)
        panes.add(self.tabs, weight=1)
        self.tabs.add(self._build_run_tab(self.tabs), text="Run")
        self.tabs.add(self._build_results_tab(self.tabs), text="Results")

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", pady=(4, 0))

        first = next(iter(self.tasks))
        self.tree.selection_set(first)
        self.tree.see(first)
        self._on_select()
        self.refresh_results()

    # -- layout ----------------------------------------------------------------------------

    def _build_tree(self, master) -> ttk.Frame:
        frame = ttk.Frame(master)
        self.tree = ttk.Treeview(frame, show="tree", selectmode="browse")
        self.tree.column("#0", width=250)
        for group, tasks in groups().items():
            parent = self.tree.insert("", "end", text=group, open=True)
            for task in tasks:
                iid = self.tree.insert(parent, "end", text=task.title)
                self.tasks[iid] = task
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        return frame

    def _build_run_tab(self, master) -> ttk.Frame:
        tab = ttk.Frame(master, padding=6)
        head = ttk.Frame(tab)
        head.pack(fill="x")
        self.title_label = ttk.Label(head, font=("Segoe UI", 12, "bold"))
        self.title_label.pack(side="left")
        self.network = ttk.Label(head, foreground="darkorange3")
        self.network.pack(side="left", padx=12)
        self.description = ttk.Label(tab, foreground="gray30", wraplength=900, justify="left")
        self.description.pack(fill="x", pady=(2, 6))

        vertical = ttk.PanedWindow(tab, orient="vertical")
        vertical.pack(fill="both", expand=True)
        form_box = ttk.Frame(vertical)
        self.form_canvas = tk.Canvas(form_box, highlightthickness=0, height=240)
        scroll = ttk.Scrollbar(form_box, orient="vertical", command=self.form_canvas.yview)
        self.form_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.form_canvas.pack(side="left", fill="both", expand=True)
        self.form_holder = ttk.Frame(self.form_canvas)
        window = self.form_canvas.create_window(0, 0, anchor="nw", window=self.form_holder)
        self.form_holder.bind(
            "<Configure>",
            lambda _: self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")),
        )
        self.form_canvas.bind(
            "<Configure>", lambda e: self.form_canvas.itemconfigure(window, width=e.width)
        )
        self.form_canvas.bind("<Enter>", lambda _: self._wheel(self.form_canvas))
        vertical.add(form_box, weight=1)

        lower = ttk.Frame(vertical)
        buttons = ttk.Frame(lower)
        buttons.pack(fill="x", pady=4)
        self.run_button = ttk.Button(buttons, text="Run", command=self.start)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        for widget in (
            self.run_button,
            self.stop_button,
            ttk.Button(buttons, text="Reset options", command=self.reset),
            ttk.Button(buttons, text="Copy command", command=self.copy_command),
            ttk.Button(buttons, text="Clear output", command=self.clear_output),
            ttk.Button(buttons, text="Save log...", command=self.save_log),
        ):
            widget.pack(side="left", padx=(0, 6))
        self.command = tk.StringVar()
        ttk.Entry(lower, textvariable=self.command, state="readonly", font=MONO).pack(fill="x")

        out_box = ttk.Frame(lower)
        out_box.pack(fill="both", expand=True, pady=(6, 0))
        self.output = tk.Text(out_box, font=MONO, wrap="none", state="disabled", undo=False)
        yscroll = ttk.Scrollbar(out_box, orient="vertical", command=self.output.yview)
        xscroll = ttk.Scrollbar(out_box, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.tag_configure("pass", foreground="white", background="forest green")
        self.output.tag_configure("fail", foreground="white", background="firebrick")
        self.output.tag_configure("error", foreground="firebrick")
        self.output.tag_configure("meta", foreground="gray45")
        vertical.add(lower, weight=3)
        return tab

    def _build_results_tab(self, master) -> ttk.Frame:
        tab = ttk.Frame(master, padding=6)
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Refresh", command=self.refresh_results).pack(side="left")
        ttk.Button(bar, text="Open folder", command=self.open_results_folder).pack(
            side="left", padx=6
        )
        panes = ttk.PanedWindow(tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.files = ttk.Treeview(panes, columns=("modified", "size"), selectmode="browse")
        self.files.heading("#0", text="File")
        self.files.heading("modified", text="Modified")
        self.files.heading("size", text="Size")
        self.files.column("#0", width=230)
        self.files.column("modified", width=130)
        self.files.column("size", width=70, anchor="e")
        self.files.bind("<<TreeviewSelect>>", self._show_result)
        panes.add(self.files, weight=0)
        box = ttk.Frame(panes)
        self.preview = tk.Text(box, font=MONO, wrap="none", state="disabled")
        yscroll = ttk.Scrollbar(box, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side="right", fill="y")
        self.preview.pack(side="left", fill="both", expand=True)
        panes.add(box, weight=1)
        return tab

    def _wheel(self, canvas: tk.Canvas) -> None:
        def scroll(event):
            if self.form_holder.winfo_height() > canvas.winfo_height():
                canvas.yview_scroll(-event.delta // 120, "units")

        canvas.bind_all("<MouseWheel>", scroll)
        canvas.bind("<Leave>", lambda _: canvas.unbind_all("<MouseWheel>"))

    # -- task selection and form -----------------------------------------------------------

    def _on_select(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected or selected[0] not in self.tasks:
            return
        iid = selected[0]
        if self.panel is not None:
            self.panel.pack_forget()
        if iid not in self.panels:
            try:
                self.panels[iid] = TaskPanel(self.form_holder, self.tasks[iid], self.update_command)
            except Exception as e:  # a broken parser should not take the window down
                messagebox.showerror("Cannot load task", f"{type(e).__name__}: {e}")
                return
        self.panel = self.panels[iid]
        self.panel.pack(fill="x", expand=True)
        self.form_canvas.yview_moveto(0)
        self.title_label.configure(text=self.panel.task.title)
        self.description.configure(text=first_paragraph(self.panel.form.description))
        self.tabs.select(0)
        self.update_command()

    def update_command(self) -> None:
        if self.panel is None:
            return
        argv, errors = self.panel.argv()
        task = self.panel.task
        self.command.set(subprocess.list2cmdline(task.command(argv)))
        online = task.online(self.panel.values())
        self.network.configure(text="Uses the network" if online else "")
        self.run_button.configure(state="disabled" if errors or self.running else "normal")

    def reset(self) -> None:
        if self.panel is not None:
            self.panel.reset()

    def copy_command(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.command.get())
        self.status.set("Command copied")

    # -- running ---------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.run is not None and not self.run.done

    def start(self) -> None:
        if self.panel is None or self.running:
            return
        argv, errors = self.panel.argv()
        if errors:
            return
        task = self.panel.task
        command = task.command(argv)
        self.clear_output()
        self.write(f"$ {subprocess.list2cmdline(command)}\n", "meta")
        try:
            self.run = Run(command)
        except OSError as e:
            self.write(f"could not start: {e}\n", "error")
            return
        self.run_task = task
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.after(POLL_MS, self._pump)

    def _pump(self) -> None:
        run = self.run
        if run is None:
            return
        pytest = self.run_task.module == "pytest"
        for line in run.drain():
            self.write(line, line_tag(line, pytest))
        if not run.done:
            self.status.set(f"Running {self.run_task.title}  {run.elapsed:.1f} s")
            self.after(POLL_MS, self._pump)
            return
        for line in run.drain(limit=10**9):
            self.write(line, line_tag(line, pytest))
        outcome = "stopped" if run.stopped else f"exit code {run.returncode}"
        self.write(f"\n[{outcome}, {run.elapsed:.1f} s]\n", "meta")
        self.status.set(f"{self.run_task.title}: {outcome} after {run.elapsed:.1f} s")
        self.stop_button.configure(state="disabled")
        self.update_command()
        self.refresh_results()

    def stop(self) -> None:
        if self.running:
            self.status.set("Stopping...")
            self.run.stop()

    def write(self, text: str, tag: str | None = None) -> None:
        at_bottom = self.output.yview()[1] >= 0.999
        self.output.configure(state="normal")
        self.output.insert("end", text, tag or ())
        self.output.configure(state="disabled")
        if at_bottom:
            self.output.see("end")

    def clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def save_log(self) -> None:
        values = self.panel.values() if self.panel else {}
        out = str(values.get("out") or "")
        initial = Path(out).with_suffix(".log") if out else RESULTS / "run.log"
        path = filedialog.asksaveasfilename(
            initialdir=str((ROOT / initial).parent),
            initialfile=initial.name,
            defaultextension=".log",
            filetypes=[("Log", "*.log"), ("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            Path(path).write_text(self.output.get("1.0", "end-1c"), encoding="utf-8")
            self.status.set(f"Saved {path}")

    def close(self) -> None:
        if self.running:
            if not messagebox.askyesno("Run in progress", "A run is going. Stop it and quit?"):
                return
            self.run.stop()
        self.root.destroy()

    # -- results ---------------------------------------------------------------------------

    def refresh_results(self) -> None:
        selected = self.files.selection()
        self.files.delete(*self.files.get_children())
        if not RESULTS.is_dir():
            return
        for folder in sorted(p for p in RESULTS.iterdir() if p.is_dir()):
            parent = self.files.insert("", "end", text=folder.name, open=True)
            files = [p for p in folder.iterdir() if p.suffix in (".json", ".log", ".txt")]
            for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
                stat = path.stat()
                self.files.insert(
                    parent, "end", iid=str(path), text=path.name,
                    values=(format_time(stat.st_mtime), format_size(stat.st_size)),
                )  # fmt: skip
        if selected and self.files.exists(selected[0]):
            self.files.selection_set(selected[0])

    def _show_result(self, _event=None) -> None:
        selected = self.files.selection()
        if not selected or not Path(selected[0]).is_file():
            return
        text = read_result(Path(selected[0]))
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def open_results_folder(self) -> None:
        selected = self.files.selection()
        target = Path(selected[0]) if selected else RESULTS
        os.startfile(target.parent if target.is_file() else target)  # Windows only


def first_paragraph(text: str) -> str:
    return text.strip().split("\n\n")[0].replace("\n", " ")


def line_tag(line: str, pytest: bool = False) -> str | None:
    if pytest and PYTEST_SUMMARY.match(line):
        return "fail" if re.search(r"\b(failed|error|errors)\b", line) else "pass"
    return "error" if FAILURE_LINE.search(line) else None


def read_result(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(data, dict) and "provenance" in data:
                data = {"provenance": data["provenance"], **data}
            text = json.dumps(data, indent=2)
    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS] + f"\n... ({len(text):,} characters, truncated)"
    return text


def format_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def format_size(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n} B"


def main() -> int:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
