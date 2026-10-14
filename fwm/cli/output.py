import json
from typing import Any, Callable, List, Sequence, Tuple, Union

import click

from fwm.repo.query import View


def echo_json(value: Any) -> None:
    click.echo(json.dumps(value, indent=2))


# What a table column shows: a key of the view, or a function of the view
Column = Union[str, Callable[[View], Any]]


def echo_table(rows: List[View], columns: Sequence[Tuple[str, Column]]) -> None:
    """
    Print views as a table.

    :param columns: (heading, what it shows) of each column.
    """
    def cell(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, list):
            return ",".join(cell(v) for v in value) if value else "-"
        return str(value)

    def value(row: View, column: Column) -> Any:
        return column(row) if callable(column) else row[column]

    table = [[heading for heading, _ in columns]] + [[cell(value(row, c)) for _, c in columns] for row in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(columns))]
    for r in table:
        click.echo("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


def summarize_sim_version(view: View) -> View:
    """A view with its `spike --version` output shortened to each component's version and commit, for reading."""
    sim_version = view.get("sim_version")
    if not sim_version:
        return view
    builds = sim_version["builds"]
    if builds is None:
        summary: Any = sim_version["raw"].strip()
    else:
        summary = {title: f"{fields['Version']} ({fields['Commit'][:12]})" for title, fields in builds.items()}
    return {**view, "sim_version": summary}


def echo_view(view: View, indent: int = 0) -> None:
    """Print a view as indented "key: value" lines."""
    pad = " " * indent
    for key, value in view.items():
        if isinstance(value, dict):
            click.echo(f"{pad}{key}:")
            echo_view(value, indent + 2)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            click.echo(f"{pad}{key}:")
            for item in value:
                click.echo(f"{pad}  -")
                echo_view(item, indent + 4)
        elif isinstance(value, list):
            click.echo(f"{pad}{key}: {', '.join(str(v) for v in value) if value else '-'}")
        else:
            click.echo(f"{pad}{key}: {'-' if value is None else value}")
