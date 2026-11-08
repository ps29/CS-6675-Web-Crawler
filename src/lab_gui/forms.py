"""Turn an argparse parser into form fields, and form values back into argv.

No Tk here, so this is unit-tested headless. Values are what the widgets hold: `bool` for a
flag (checked = the flag is given, so `--no-collude` checked passes `--no-collude`) and `str`
for everything else. A blank or unchanged value is left out of argv, so the program sees its
own default."""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from typing import Any

FLAG_ACTIONS = (argparse._StoreTrueAction, argparse._StoreFalseAction)
SKIPPED = (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction)


@dataclass(frozen=True)
class Field:
    dest: str
    flag: str | None  # None for a positional
    kind: str  # "flag", "choice", "list" or "text"
    default: Any
    choices: tuple | None = None
    type: Any = None
    help: str = ""
    sub: bool = False  # belongs to the subcommand, so it goes after it in argv

    @property
    def label(self) -> str:
        return self.flag or self.dest

    def initial(self) -> bool | str:
        if self.kind == "flag":
            return False
        if self.default is None:
            return ""
        if self.kind == "list":
            return " ".join(map(str, self.default))
        return str(self.default)


@dataclass(frozen=True)
class Form:
    fields: tuple[Field, ...]
    subcommand: str | None = None
    description: str = ""

    def initial(self) -> dict[str, bool | str]:
        return {f.dest: f.initial() for f in self.fields}


def _field(action: argparse.Action, sub: bool) -> Field:
    flag = max(action.option_strings, key=len) if action.option_strings else None
    help_ = "" if action.help in (None, argparse.SUPPRESS) else action.help
    with contextlib.suppress(KeyError, ValueError, TypeError):  # a literal % in the help
        help_ = help_ % vars(action)
    if isinstance(action, FLAG_ACTIONS):
        kind = "flag"
    elif action.nargs in ("+", "*"):
        kind = "list"
    elif action.choices:
        kind = "choice"
    else:
        kind = "text"
    choices = tuple(action.choices) if action.choices else None
    return Field(action.dest, flag, kind, action.default, choices, action.type, help_, sub)


def _actions(parser: argparse.ArgumentParser):
    return [a for a in parser._actions if not isinstance(a, SKIPPED)]


def build_form(parser: argparse.ArgumentParser, subcommand: str | None = None) -> Form:
    fields = [_field(a, False) for a in _actions(parser)]
    description = parser.description or ""
    if subcommand:
        subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        subparser = subs.choices[subcommand]
        fields += [_field(a, True) for a in _actions(subparser)]
        helps = {a.dest: a.help for a in subs._choices_actions}
        description = helps.get(subcommand) or subparser.description or description
    return Form(tuple(fields), subcommand, description.strip())


def _convert(field: Field, text: str) -> Any:
    value = field.type(text) if field.type else text
    if field.choices and value not in field.choices:
        raise ValueError(f"choose from {', '.join(map(str, field.choices))}")
    return value


def _check(field: Field, value: bool | str) -> tuple[list[str], str | None]:
    """The argv for one field and an error message, if any."""
    if field.kind == "flag":
        return ([field.flag] if value else []), None
    text = str(value).strip()
    if not text:
        if field.flag is None and field.default is None:
            return [], "required"
        return [], None
    parts = text.split() if field.kind == "list" else [text]
    try:
        converted = [_convert(field, p) for p in parts]
    except (ValueError, TypeError, argparse.ArgumentTypeError) as e:
        name = getattr(field.type, "__name__", "value")
        return [], str(e) if "choose from" in str(e) else f"expected {name}, got {text!r}"
    default = field.default if field.kind == "list" else [field.default]
    if field.flag is None:
        return parts, None
    if default is not None and (
        converted == list(default) or list(map(str, converted)) == list(map(str, default))
    ):  # str() too, for defaults such as a Path
        return [], None
    return [field.flag, *parts], None


def to_argv(form: Form, values: dict[str, bool | str]) -> tuple[list[str], dict[str, str]]:
    """argv for these form values, and an error per invalid field (empty when valid)."""
    head: list[str] = []
    tail: list[str] = []
    errors: dict[str, str] = {}
    for field in form.fields:
        argv, error = _check(field, values.get(field.dest, field.initial()))
        if error:
            errors[field.dest] = error
        (tail if field.sub else head).extend(argv)
    if form.subcommand:
        head.append(form.subcommand)
    return head + tail, errors
