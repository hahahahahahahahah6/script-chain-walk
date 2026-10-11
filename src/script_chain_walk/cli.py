"""Command-line interface for script-chain-walk."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .graph import build_graph, render_json, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="script-chain-walk",
        description="Trace cross-file script call chains: build a dependency "
                    "graph of shell/Python scripts, find broken chains and "
                    "call cycles.",
    )
    parser.add_argument("directory", nargs="?",
                        help="directory to scan (default: current directory)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="output format (default: text)")
    parser.add_argument("--entry", metavar="SCRIPT",
                        help="only show the chain starting from SCRIPT "
                             "(path relative to the scanned directory)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any broken chain or cycle is found "
                             "(for CI / agent guardrails)")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    directory = os.path.abspath(args.directory or os.getcwd())
    if not os.path.isdir(directory):
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2

    graph = build_graph(directory)

    if args.entry:
        entry = os.path.normpath(args.entry)
        if entry not in graph["nodes"]:
            print(f"error: {args.entry} is not a known script in {directory}",
                  file=sys.stderr)
            return 2
        graph["entry_points"] = [entry]

    if args.format == "json":
        print(render_json(graph), end="")
    else:
        print(render_text(graph), end="")

    if args.check and (graph["broken"] or graph["cycles"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
