"""Build and render the cross-file script call graph."""

from __future__ import annotations

import json
import os

from .parse import SKIP_DIRS, analyze_file, is_script


def scan(root: str) -> dict[str, str]:
    """Find all scripts under *root*; returns {relative_path: absolute_path}."""
    scripts: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if is_script(full):
                rel = os.path.relpath(full, root)
                scripts[rel] = full
    return scripts


def resolve_target(root: str, scripts: dict[str, str],
                   caller_rel: str, target: str) -> tuple[str | None, str]:
    """Resolve a call target to a path relative to *root*.

    Returns (resolved_rel_or_None, how) where *how* is one of
    ``relative`` (next to the caller), ``searched`` (unique basename match
    elsewhere in the tree), ``absolute`` (absolute path on disk),
    ``ambiguous`` (several basename matches) or ``missing``.
    """
    if os.path.isabs(target):
        if os.path.isfile(target):
            rel = os.path.relpath(target, root)
            return (rel if not rel.startswith("..") else target), "absolute"
        return None, "missing"
    caller_dir = os.path.dirname(os.path.join(root, caller_rel))
    candidate = os.path.normpath(os.path.join(caller_dir, target))
    if os.path.isfile(candidate):
        return os.path.relpath(candidate, root), "relative"
    base = os.path.basename(target)
    matches = sorted(rel for rel in scripts if os.path.basename(rel) == base)
    if len(matches) == 1:
        return matches[0], "searched"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "missing"


def build_graph(root: str) -> dict:
    """Scan *root* and build the call graph.

    Returns a dict with ``nodes``, ``edges``, ``broken``, ``cycles`` and
    ``entry_points``. Paths are relative to *root*.
    """
    root = os.path.abspath(root)
    scripts = scan(root)
    edges: dict[tuple[str, str], dict] = {}
    broken: list[dict] = []

    for caller_rel in sorted(scripts):
        for target, lineno, kind in analyze_file(scripts[caller_rel]):
            resolved, how = resolve_target(root, scripts, caller_rel, target)
            if resolved is None:
                broken.append({
                    "caller": caller_rel,
                    "target": target,
                    "line": lineno,
                    "kind": kind,
                    "reason": how,  # missing | ambiguous
                })
                continue
            key = (caller_rel, resolved)
            edge = edges.setdefault(key, {
                "from": caller_rel, "to": resolved,
                "lines": [], "kind": kind, "resolved_via": how,
            })
            if lineno not in edge["lines"]:
                edge["lines"].append(lineno)

    adjacency: dict[str, list[str]] = {rel: [] for rel in scripts}
    for (src, dst) in edges:
        if dst not in adjacency:
            adjacency[dst] = []
        if dst not in adjacency[src]:
            adjacency[src].append(dst)
    for node in adjacency:
        adjacency[node].sort()

    cycles = find_cycles(adjacency)

    called = {dst for _, dst in edges}
    entry_points = sorted(rel for rel in scripts if rel not in called)

    return {
        "root": root,
        "nodes": sorted(scripts),
        "edges": [edges[k] for k in sorted(edges)],
        "broken": sorted(broken, key=lambda b: (b["caller"], b["line"])),
        "cycles": cycles,
        "entry_points": entry_points,
    }


def find_cycles(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Return simple cycles found via iterative DFS (white/gray/black)."""
    cycles: list[list[str]] = []
    color: dict[str, int] = {}  # 1 = gray (on stack), 2 = black (done)
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in adjacency.get(node, []):
            if color.get(nxt) == 1:
                cycle = stack[stack.index(nxt):] + [nxt]
                if cycle not in cycles:
                    cycles.append(cycle)
            elif color.get(nxt) != 2:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for node in sorted(adjacency):
        if color.get(node) != 2:
            visit(node)
    return cycles


def render_text(graph: dict) -> str:
    """Render the call graph as an ASCII tree per entry point, plus a report."""
    lines: list[str] = []
    adjacency: dict[str, list[dict]] = {}
    for edge in graph["edges"]:
        adjacency.setdefault(edge["from"], []).append(edge)
    for lst in adjacency.values():
        lst.sort(key=lambda e: (e["to"], e["lines"]))

    def tree(node: str, prefix: str, seen: tuple[str, ...]) -> None:
        children = adjacency.get(node, [])
        for idx, edge in enumerate(children):
            last = idx == len(children) - 1
            branch = "`-- " if last else "|-- "
            dst = edge["to"]
            line = edge["lines"][0]
            marker = ""
            if dst in seen:
                marker = "  [CYCLE]"
            lines.append(f"{prefix}{branch}{dst}  (line {line}, {edge['kind']}){marker}")
            if dst not in seen:
                tree(dst, prefix + ("    " if last else "|   "), seen + (dst,))

    entries = graph["entry_points"] or graph["nodes"]
    for entry in entries:
        lines.append(entry)
        tree(entry, "", (entry,))
        lines.append("")

    lines.append("=" * 40)
    lines.append(f"scripts: {len(graph['nodes'])}   "
                 f"edges: {len(graph['edges'])}   "
                 f"broken: {len(graph['broken'])}   "
                 f"cycles: {len(graph['cycles'])}")
    if graph["broken"]:
        lines.append("")
        lines.append("BROKEN CHAINS (call target not found):")
        for b in graph["broken"]:
            lines.append(f"  {b['caller']}:{b['line']} -> {b['target']} "
                         f"({b['kind']}, {b['reason']})")
    if graph["cycles"]:
        lines.append("")
        lines.append("CYCLES:")
        for cycle in graph["cycles"]:
            lines.append("  " + " -> ".join(cycle))
    return "\n".join(lines).rstrip() + "\n"


def render_json(graph: dict) -> str:
    payload = {
        "root": graph["root"],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "broken": graph["broken"],
        "cycles": graph["cycles"],
        "entry_points": graph["entry_points"],
        "summary": {
            "scripts": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "broken": len(graph["broken"]),
            "cycles": len(graph["cycles"]),
        },
    }
    return json.dumps(payload, indent=2) + "\n"
