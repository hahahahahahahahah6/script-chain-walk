"""Static extraction of script-to-script calls from shell and Python sources.

Standard library only. The extraction is heuristic but conservative: a call
is only recorded when the target token looks like a script path (starts with
./, ../ or /, or ends with a known script extension).
"""

from __future__ import annotations

import ast
import os
import re
import shlex

SHELL_EXTS = (".sh", ".bash", ".zsh", ".ksh", ".dash")
SCRIPT_EXTS = SHELL_EXTS + (".py", ".pl", ".rb", ".js")

INTERPRETERS = {
    "bash", "sh", "dash", "zsh", "ksh",
    "python", "python2", "python3", "perl", "ruby", "node",
}

SUBPROCESS_FUNCS = {"run", "call", "check_call", "check_output", "Popen"}
OS_SHELL_FUNCS = {"system", "popen"}
OS_EXEC_FUNCS = {
    "execv", "execvp", "execve", "execl", "execlp", "execvpe",
    "spawnl", "spawnlp", "spawnlpe", "spawnv", "spawnvp", "spawnvpe",
}

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
             ".hg", ".svn", "dist", "build", "target"}


def strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment, ignoring ``#`` inside quotes."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and i + 1 < len(line):
                out.append(ch)
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
        else:
            if ch in ("'", '"'):
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def join_continuations(text: str) -> list[str]:
    """Join backslash line continuations; returns logical lines."""
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            buf += stripped[:-1] + " "
        else:
            logical.append(buf + raw)
            buf = ""
    if buf:
        logical.append(buf)
    return logical


def _split_segments(command: str) -> list[str]:
    """Split a shell command string on ;, &&, || and |, respecting quotes."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and i + 1 < len(command):
                buf.append(ch)
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        two = command[i:i + 2]
        if two in ("&&", "||"):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in (";", "|"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return segments


def looks_like_script(token: str) -> bool:
    """Conservative check: does this token look like a script invocation?"""
    if not token or token.startswith("-"):
        return False
    base = os.path.basename(token)
    if base.endswith(SCRIPT_EXTS):
        return True
    if token.startswith(("./", "../", "/")):
        return True
    return False


def _is_interpreter(token: str) -> bool:
    base = os.path.basename(token)
    return token in INTERPRETERS or base in INTERPRETERS


def calls_from_tokens(tokens: list[str]) -> list[tuple[str, str]]:
    """Extract (target, kind) calls from an argv-style token list."""
    calls: list[tuple[str, str]] = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if _is_interpreter(tok):
            # interpreter [flags...] script
            j = i + 1
            while j < n and tokens[j].startswith("-") and tokens[j] != "-":
                j += 1
            if j < n:
                calls.append((tokens[j], "exec"))
            i = j + 1
            continue
        if tok == "source" or tok == ".":
            if i + 1 < n:
                calls.append((tokens[i + 1], "source"))
            i += 2
            continue
        if tok == "exec":
            if i + 1 < n and looks_like_script(tokens[i + 1]):
                calls.append((tokens[i + 1], "exec"))
            i += 2
            continue
        if looks_like_script(tok):
            calls.append((tok, "exec"))
            i += 1
            continue
        i += 1
    return calls


def analyze_command_string(command: str) -> list[tuple[str, str]]:
    """Extract script calls from a shell command string."""
    calls: list[tuple[str, str]] = []
    for segment in _split_segments(command):
        segment = strip_comment(segment).strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        calls.extend(calls_from_tokens(tokens))
    return calls


def analyze_shell(text: str) -> list[tuple[str, int, str]]:
    """Extract (target, line, kind) calls from shell script source."""
    calls: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(join_continuations(text), start=1):
        for target, kind in analyze_command_string(line):
            calls.append((target, lineno, kind))
    return calls


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _const_argv(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        items: list[str] = []
        for elt in node.elts:
            s = _const_str(elt)
            if s is None:
                return None
            items.append(s)
        return items
    return None


class _PythonCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call) -> None:
        func = node.func
        mod: str | None = None
        name: str | None = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            mod, name = func.value.id, func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if not name or not node.args:
            return
        first = node.args[0]

        if (mod == "subprocess" or mod is None) and name in SUBPROCESS_FUNCS:
            argv = _const_argv(first)
            if argv is not None:
                for target, kind in calls_from_tokens(argv):
                    self.calls.append((target, node.lineno, kind))
            else:
                cmd = _const_str(first)
                if cmd:
                    for target, kind in analyze_command_string(cmd):
                        self.calls.append((target, node.lineno, kind))
        elif mod == "os" and name in OS_SHELL_FUNCS:
            cmd = _const_str(first)
            if cmd:
                for target, kind in analyze_command_string(cmd):
                    self.calls.append((target, node.lineno, kind))
        elif mod == "os" and name in OS_EXEC_FUNCS:
            target = _const_str(first)
            if target and looks_like_script(target):
                self.calls.append((target, node.lineno, "exec"))
        elif mod is None and name == "exec":
            # exec(open("other.py").read())
            if (isinstance(first, ast.Call)
                    and isinstance(first.func, ast.Name)
                    and first.func.id == "open"
                    and first.args):
                target = _const_str(first.args[0])
                if target and looks_like_script(target):
                    self.calls.append((target, node.lineno, "exec"))
        elif mod == "runpy" and name == "run_path":
            target = _const_str(first)
            if target and looks_like_script(target):
                self.calls.append((target, node.lineno, "exec"))


def analyze_python(text: str) -> list[tuple[str, int, str]]:
    """Extract (target, line, kind) calls from Python source via the AST."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    visitor = _PythonCallVisitor()
    visitor.visit(tree)
    return visitor.calls


def is_script(path: str) -> bool:
    """True if the file looks like a script (extension or shebang)."""
    base = os.path.basename(path)
    if base.endswith(SCRIPT_EXTS):
        return True
    try:
        with open(path, "rb") as fh:
            if fh.read(2) == b"#!":
                return True
    except OSError:
        return False
    return False


def analyze_file(path: str) -> list[tuple[str, int, str]]:
    """Dispatch to the right analyzer based on filename / shebang."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    base = os.path.basename(path)
    if base.endswith(".py"):
        return analyze_python(text)
    first = text.split("\n", 1)[0] if text else ""
    if "python" in first and first.startswith("#!"):
        return analyze_python(text)
    return analyze_shell(text)
