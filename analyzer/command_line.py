"""B1's command line: what the agent's one tool, ``cc(command)``, accepts.

The syntax is the one B1 was benchmarked on (``python3 -m analyzer.cli --session <dir> <command>``), without the
program name and the ``--session`` argument: the run binds the session server-side. A line is split with ``shlex`` and
parsed with argparse, then run in this process by ``analyzer.b1_commands.B1Run`` against the request's reader session.
What comes back is what the CLI printed: the command's JSON result, or ``ERROR: <message>`` (including quoting and
usage errors, so the model can fix the line and try again).

    sql "SELECT ..."
    lookup payer_name "north"
    escalate "<why sem.* cannot answer>"
    list                         # lossless.* relations, after escalate
    describe lossless.<relation> # after escalate
    answer --sql "SELECT ..." --text "<one-paragraph answer>"
    refuse "<reason>"
    ask --questions '[{"header": ..., "question": ..., "options": [...]}]'   (or: ask "<one plain question>")
"""
from __future__ import annotations

import argparse
import json
import shlex

from analyzer.tools import ToolError


class CommandLineError(ToolError):
    """The line could not be parsed: bad quoting, an unknown command or wrong arguments."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CommandLineError(f"{message}. Usage: {self.format_usage().strip().removeprefix('usage: ')}")

    def exit(self, status=0, message=None):
        raise CommandLineError((message or "invalid command").strip())


def _parser() -> _Parser:
    parser = _Parser(prog="cc", add_help=False, description="B1 analyzer commands")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("sql", add_help=False).add_argument("sql")
    lookup = commands.add_parser("lookup", add_help=False)
    lookup.add_argument("dimension")
    lookup.add_argument("text")
    commands.add_parser("escalate", add_help=False).add_argument("reason")
    commands.add_parser("list", add_help=False)
    commands.add_parser("describe", add_help=False).add_argument("relation")
    answer = commands.add_parser("answer", add_help=False)
    answer.add_argument("--sql", required=True)
    answer.add_argument("--text", default="")
    commands.add_parser("refuse", add_help=False).add_argument("reason")
    ask = commands.add_parser("ask", add_help=False)
    ask.add_argument("question", nargs="?")
    ask.add_argument("--questions")
    return parser


PARSER = _parser()


def parse_command_line(line: str) -> tuple[str, dict]:
    """``(command, arguments)`` for ``B1Run.run_command``, or ``CommandLineError``."""
    try:
        words = shlex.split(line or "")
    except ValueError as exc:
        raise CommandLineError(f"could not split the command line: {exc}. Quote each argument; put SQL in double "
                               "quotes and JSON in single quotes") from None
    if not words:
        raise CommandLineError("empty command line")
    parsed = vars(PARSER.parse_args(words))
    command = parsed.pop("command")
    arguments = {key: value for key, value in parsed.items() if value is not None}
    if command == "ask" and "questions" in arguments:
        try:
            arguments["questions"] = json.loads(arguments["questions"])
        except json.JSONDecodeError as exc:
            raise CommandLineError(f"--questions is not valid JSON: {exc.msg}") from None
    return command, arguments
