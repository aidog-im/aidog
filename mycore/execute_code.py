"""mycore.execute_code — folded multi-step execution (car 2-D7).

One tool, two shapes (the model picks exactly one — the handler refuses
anything else honestly):

  * steps: an ordered list of {name, args} tool calls dispatched back-to-back
    against the SAME registry in ONE round trip. Each step's result is folded
    into a labelled block, so N model->tool->model round trips collapse into
    one (the token/latency win). Steps may NOT name `execute_code` or
    `delegate` (no nested folding, no nested delegation — the fold is always
    a leaf). A failing step is reported inside the fold; later steps still
    run (static args — one failure cannot corrupt the rest).

  * code: Python source executed ONCE in a child process
    (`python -m mycore.execute_code --child <spec>`) with a small helper API:
    read/grep/glob/write/edit/run/fetch — each enforcing the same allowed
    roots as the registry tools, and the write helpers taking pre-write
    checkpoints (state_dir is threaded through exactly like write_file).

Trust model (documented honestly, no sandbox claims): arbitrary Python can
do anything `run_bash` already allows (same user, same process rights) —
the helpers apply the path policy, plain os.* does not, exactly like a
shell redirect escapes write_file. Timeout/output caps mirror run_bash.

Stdlib only; English comments (repo i18n policy).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_STEPS = 8                       # one fold: bounded work per round trip
STEP_CAP = 2500                     # per-step preview inside the fold
MAX_CODE_CHARS = 30000              # source cap before we even spawn
CODE_TIMEOUT = 120                  # parity with tools.BASH_TIMEOUT

NESTED_FOLD = ("execute_code", "delegate")


class ExecError(Exception):
    """Raised for caller mistakes; tools.dispatch renders it as `ERROR: ...`."""


# ----------------------------------------------------------------- steps ----

def run_steps(steps: list, registry) -> str:
    """Dispatch an ordered tool-call list, fold every result into one block.
    Raises ExecError for structural mistakes (validation), never for a
    step's own failure — that is reported in-place, honestly."""
    if not isinstance(steps, list) or not steps:
        raise ExecError("steps must be a non-empty array of {name, args} objects")
    if len(steps) > MAX_STEPS:
        raise ExecError(f"at most {MAX_STEPS} steps per call (got {len(steps)})")
    out: list[str] = []
    total = len(steps)
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ExecError(f"step {i} must be an object with a name")
        name = step.get("name")
        if not isinstance(name, str) or not name:
            raise ExecError(f"step {i} needs a non-empty tool name")
        if name in NESTED_FOLD:
            raise ExecError(f"step {i} cannot be {name} (no nested folding)")
        args = step.get("args", {})
        if not isinstance(args, dict):
            raise ExecError(f"step {i} args must be a JSON object")
        result = registry.dispatch(name, args)   # never raises
        ok = not result.startswith("ERROR:")
        if len(result) > STEP_CAP:
            result = (result[:STEP_CAP]
                      + f"... [+{len(result) - STEP_CAP} chars folded]")
        out.append(f"[{i}/{total} {name}] {'ok' if ok else 'ERROR'}\n{result}")
    return "\n".join(out)


# ------------------------------------------------------------------ code ----

def run_code(code: str, roots: tuple[str, ...] | list[str],
             state_dir: str | Path | None = None,
             timeout: int = CODE_TIMEOUT) -> str:
    """Execute Python source in a child process and report it like run_bash
    (exit line + stdout + stderr). Raises ExecError for caller mistakes and
    for a timeout — everything else is the child's own honest output."""
    if not isinstance(code, str) or not code.strip():
        raise ExecError("code must be a non-empty Python source string")
    if len(code) > MAX_CODE_CHARS:
        raise ExecError(f"code exceeds {MAX_CODE_CHARS} chars")
    if isinstance(timeout, bool) or not isinstance(timeout, int) \
            or not 1 <= timeout <= CODE_TIMEOUT:
        raise ExecError(f"timeout must be an integer in 1..{CODE_TIMEOUT}")
    spec = {"roots": list(roots), "state_dir": str(state_dir) if state_dir else None}
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", prefix="aidog_exec_",
                                      delete=False, encoding="utf-8")
    try:
        tmp.write(code)
        tmp.close()
        cmd = [sys.executable, "-m", "mycore.execute_code", "--child",
               tmp.name, json.dumps(spec)]
        proc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ExecError(f"code timed out after {timeout}s") from None
    except OSError as e:
        raise ExecError(f"cannot spawn interpreter ({type(e).__name__})") from None
    finally:
        try:
            Path(tmp.name).unlink()
        except OSError:
            pass
    out = (proc.stdout or "") + (("\n--- stderr ---\n" + proc.stderr)
                                 if proc.stderr else "")
    header = f"exit={proc.returncode}"
    return f"{header}\n{out}".rstrip() or f"{header} (no output)"


def _child(code_path: str, spec_json: str) -> int:
    """Child-process entry: build the helper namespace and exec the source.
    ToolError from a helper becomes one honest ERROR line + exit 1 (the
    parent folds that back exactly like any other tool failure)."""
    from .tools import ToolError, _edit_file, _glob, _grep, _http_fetch, \
        _read_file, _run_bash, _write_file

    try:
        spec = json.loads(spec_json)
        roots = tuple(spec.get("roots") or [])
        state = spec.get("state_dir")
        source = Path(code_path).read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError) as e:
        print(f"ERROR: child setup failed ({type(e).__name__})", file=sys.stderr)
        return 1

    def _bind(fn, *fixed, **kw):
        def call(*args, **kwargs):
            return fn(*fixed, *args, **kw, **kwargs)
        return call

    env = {
        "__name__": "__main__",
        "read": _bind(_read_file, roots=roots),
        "grep": _bind(_grep, roots=roots),
        "glob": _bind(_glob, roots=roots),
        "write": _bind(_write_file, roots=roots, state_dir=state),
        "edit": _bind(_edit_file, roots=roots, state_dir=state),
        "run": _bind(_run_bash, roots=roots),
        "fetch": _bind(_http_fetch),
    }
    try:
        exec(compile(source, "<execute_code>", "exec"), env)  # noqa: S102
    except ToolError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except BaseException as e:  # noqa: BLE001 — honest traceback, exit 1
        import traceback
        traceback.print_exc()
        return 1
    return 0


# -------------------------------------------------------------- entry api ---

def execute(steps: list | None = None, code: str | None = None,
            timeout: int = CODE_TIMEOUT, registry=None,
            roots: tuple[str, ...] | list[str] = (),
            state_dir: str | Path | None = None) -> str:
    """The `execute_code` tool handler: exactly one of steps/code."""
    if (steps is None) == (code is None):
        raise ExecError("provide exactly one of steps or code")
    if steps is not None:
        if registry is None:
            raise ExecError("steps folding needs a registry")
        return run_steps(steps, registry)
    return run_code(code, roots, state_dir, timeout)


# ------------------------------------------------------------ child main ----

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "--child":
        return _child(argv[1], argv[2])
    print("usage: python -m mycore.execute_code --child <code.py> <spec.json>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
