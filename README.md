# script-chain-walk

Trace **cross-file script call chains**. Point it at a directory of shell and
Python scripts and it builds the full call graph — `a.sh` calls `b.py` calls
`c.sh` — then flags **broken chains** (calls to files that don't exist) and
**call cycles**. Standard library only, zero dependencies.

## Install

```bash
pip install script-chain-walk
```

## Usage

```bash
# Text tree of every call chain in ./scripts
walk ./scripts

# Machine-readable output
walk ./scripts --format json

# Only the chain starting from one entry point
walk ./scripts --entry deploy.sh

# CI / guardrail mode: exit 1 if any broken chain or cycle is found
walk ./scripts --check
```

Example output:

```
deploy.sh
|-- migrate.py  (line 3, exec)
|   `-- seed.sh  (line 12, exec)
`-- notify.sh  (line 5, exec)

========================================
scripts: 4   edges: 3   broken: 1   cycles: 0

BROKEN CHAINS (call target not found):
  notify.sh:8 -> ./cleanup.sh (exec, missing)
```

What it detects:

- **Shell**: `bash foo.sh`, `./foo.sh`, `sh -x foo.sh`, `source lib.sh`,
  `. lib.sh`, `python3 bar.py`, `exec ./foo.sh` (comments ignored,
  line continuations joined).
- **Python** (parsed with the real AST, not regex): `subprocess.run(...)`,
  `os.system(...)` / `os.popen(...)`, `os.execv(...)` and friends,
  `exec(open("other.py").read())`, `runpy.run_path("other.py")`.
- **Resolution**: relative to the caller first, then a unique basename match
  anywhere in the tree; absolute paths honored too. Unresolvable targets are
  reported as broken (or ambiguous when several files share the name).

## Differentiation

Static analyzers like ShellCheck or Pylint look at **one file at a time** —
they will happily pass a script that calls `./deploy.sh` when `deploy.sh`
was deleted last month. `script-chain-walk` is the **call map across files**:
which script triggers which, where the chain dead-ends, and where it loops
back on itself.

If you run coding agents, this is the missing piece of script-behavior
auditing: before trusting an agent's shell output, `walk --check` its script
directory and see the actual invocation graph — dead scripts an agent keeps
"calling", and accidental recursion — instead of reading each file by hand.

## How it works

1. Recursively finds scripts (by extension or shebang).
2. Extracts calls: shell lines via quote-aware tokenizing, Python via `ast`.
3. Resolves every target against the directory tree.
4. DFS over the graph for cycles; in-degree zero for entry points.
5. Renders a text tree or JSON; `--check` turns findings into an exit code.

## License

MIT. See [LICENSE](LICENSE).
