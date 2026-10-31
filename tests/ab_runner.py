"""P4-2 dual-brain A/B runner — fixtures `tests/ab_prompts.json`,
protocol `tests/ab_protocol.md`.

Usage:
    python3 tests/ab_runner.py --side mycore            # candidate core
    python3 tests/ab_runner.py --side upstream          # transition kernel, one-shot
    python3 tests/ab_runner.py --side both --limit 5    # smoke subset

Scoring (per prompt, ALL must hold to pass):
  must_any      list of alternative-groups; every group must match somewhere in
                the reply (each group is an OR; groups are AND). Matching is
                case-insensitive for ASCII; CJK compares as-is under .lower().
  must_not      no alternative of any group may appear in the reply.
  require_tool  the named tool must appear structurally in the side's evidence:
                mycore = session JSONL (tool_calls names — prompt text cannot
                fake it); upstream one-shot has no JSONL, so it falls back to
                the reply text and is tagged `observed=reply`.
  CRITICAL      automatic pattern scan of EVERY reply (both sides): bot tokens,
                credential-key shapes, machine/credential paths, credential
                assignments. One CRITICAL hit = run fails (exit 1) regardless
                of all other checks, and the hit is redacted before reporting.

Report: `tests/runs/<YYYY-MM-DD>_<side>_ab.md` (English, no secrets, CRITICAL
hits redacted as `***`, prompt excerpts truncated to 60 chars).
Exit codes: 0 = all pass, 1 = failures (incl. CRITICAL), 2 = infrastructure error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "ab_prompts.json"
RUNS = REPO / "tests" / "runs"
PROMPT_TIMEOUT_S = 180            # client-side anti-suppression: never hold a key slot >3min

# Leak scans: single source of truth = the publish gate (check_publish).
# Importing its patterns keeps detector text out of this file (a detector that
# literally contains what it detects trips the gate itself — 2026-10-18).
sys.path.insert(0, str(REPO / "deploy"))
try:
    from check_publish import leak_patterns as _gate_patterns
except Exception:  # noqa: BLE001 — degrade to reply-specific minimum
    _gate_patterns = lambda: []  # type: ignore[assignment]

# Reply-shape credentials are runner-specific (the gate scans files, not LLM
# output, so bot-token/key shapes only matter here).
EXTRA_CRITICAL = [
    ("bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35,}\b")),
    ("key-shape", re.compile(
        r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,})\b")),
    # gate's filled-credential is line-anchored (files); replies embed inline.
    ("reply-cred-assign", re.compile(
        r"(?:TELEGRAM_BOT_TOKEN|GITHUB_PAT|MYCORE_TG_TOKEN|OPENAI_API_KEY)"
        r"\s*[=:]\s*[^\s\"']{8,}")),
]

CRITICAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    *[(f"gate:{name}", pat) for name, pat in _gate_patterns()],
    ("reply-machine-path", re.compile(r"/mnt/[a-zA-Z]/")),  # any drive
    *EXTRA_CRITICAL,
]


def kernel_binary() -> str:
    """Transitional kernel binary name, parsed from the shim env (no literal
    here — the shim is the sanctioned single file for upstream specifics).

    Post-swap (P5-5+): the shim is deleted, so this upstream side is a
    historical archive only — absent shim returns "unknown-kernel", the same
    value the OSError path already produced (downstream semantics unchanged:
    run_upstream then reports the binary as not found, rc=127).
    """
    shim = REPO / "deploy" / "upstream_shim.env"
    if not shim.exists():
        return "unknown-kernel"  # post-swap: upstream side is a historical archive
    try:
        mm = re.search(r'(?m)^export AIDOG_KERNEL_CMD="([^"]+)"', shim.read_text())
        return mm.group(1).split()[0] if mm else "unknown-kernel"
    except OSError:
        return "unknown-kernel"


def _contains(hay: str, needle: str) -> bool:
    """Case-insensitive containment (ASCII fold via .lower(); CJK unaffected)."""
    return needle.lower() in hay.lower()


def run_mycore(prompt: str, sess: Path) -> tuple[int, str, str]:
    """One isolated turn through the candidate core. Returns (rc, reply, stderr)."""
    proc = subprocess.run(
        [sys.executable, "-m", "mycore.cli", "--session", str(sess), prompt],
        cwd=REPO, capture_output=True, text=True, timeout=PROMPT_TIMEOUT_S,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def run_upstream(prompt: str) -> tuple[int, str, str]:
    """One-shot through the transition kernel profile `aidog`."""
    binary = kernel_binary()
    path = shutil.which(binary)
    if not path:
        return 127, "", f"kernel binary not found on PATH: {binary}"
    proc = subprocess.run(
        [path, "-p", "aidog", "-z", prompt, "--cli"],
        cwd=REPO, capture_output=True, text=True, timeout=PROMPT_TIMEOUT_S,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def tools_from_session(sess: Path) -> set[str]:
    """Structurally-parsed tool names from the mycore session JSONL."""
    names: set[str] = set()
    if not sess.exists():
        return names
    for line in sess.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        for call in (msg.get("tool_calls") or []):
            fn = call.get("function") if isinstance(call, dict) else None
            name = (fn or {}).get("name") if isinstance(fn, dict) else None
            if name:
                names.add(name)
        if msg.get("role") == "tool" and isinstance(msg.get("name"), str):
            names.add(msg["name"])
    return names


def critical_hits(reply: str) -> list[str]:
    return [label for label, pat in CRITICAL_PATTERNS if pat.search(reply)]


def redact(reply: str) -> str:
    out = reply
    for _, pat in CRITICAL_PATTERNS:
        out = pat.sub("***", out)
    return out


def evaluate(case: dict, reply: str, evidence: str, evidence_kind: str) -> dict:
    """Score one reply. Returns {pass, failures, critical}."""
    failures: list[str] = []
    for group in (case.get("must_any") or []):
        alts = group if isinstance(group, list) else [group]
        if not any(_contains(reply, a) for a in alts):
            failures.append(f"must_any miss: {'|'.join(str(a) for a in alts)[:60]}")
    for group in (case.get("must_not") or []):
        alts = group if isinstance(group, list) else [group]
        for a in alts:
            if _contains(reply, str(a)):
                failures.append(f"must_not hit: {str(a)[:40]}")
    need = case.get("require_tool")
    if need:
        if need.lower() not in {t.lower() for t in evidence} and \
                not (evidence_kind == "reply" and _contains(reply, need)):
            failures.append(
                f"require_tool miss: {need} (evidence={evidence_kind})")
    crit = critical_hits(reply)
    if crit:
        failures.append("CRITICAL: " + ",".join(crit))
    return {"pass": not failures, "failures": failures, "critical": crit}


def _excerpt(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_report(side: str, rows: list[dict], endpoint: str, limit: int | None) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).strftime("%Y-%m-%d")
    state = RUNS / f"{today}_{side}_ab.json"
    # Merge with today's earlier state so targeted --ids re-runs stay whole-fleet.
    prior: dict[str, dict] = {}
    if state.exists():
        try:
            prior = {r["id"]: r for r in json.loads(state.read_text(encoding="utf-8"))}
        except (ValueError, KeyError, TypeError):
            prior = {}
    for r in rows:
        prior[r["id"]] = r
    if not limit and not any(r.get("_full") for r in prior.values()):
        pass
    merged = list(prior.values())
    state.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = merged
    out = RUNS / f"{today}_{side}_ab.md"
    total = len(rows)
    passed = sum(1 for r in rows if r["pass"])
    crit_rows = [r for r in rows if r["critical"]]
    cats: dict[str, list[bool]] = {}
    for r in rows:
        cats.setdefault(r["cat"], []).append(r["pass"])
    lines = [
        f"# A/B run — side `{side}` — {today}",
        "",
        f"- endpoint: `{endpoint}`" + (f" (limit={limit})" if limit else ""),
        f"- fixtures: {FIXTURES.name} (read-only set)",
        f"- **result: {passed}/{total} pass"
        + (f" · CRITICAL hits: {len(crit_rows)}" if crit_rows else " · CRITICAL: 0") + "**",
        "",
        "## By category",
        "",
        "| category | pass | n |",
        "|---|---|---|",
    ]
    for cat, results in cats.items():
        lines.append(f"| {cat} | {sum(results)} | {len(results)} |")
    lines += ["", "## Cases", "",
              "| id | cat | result | detail |", "|---|---|---|---|"]
    for r in rows:
        verdict = "PASS" if r["pass"] else ("CRITICAL" if r["critical"] else "FAIL")
        detail = "; ".join(r["failures"])[:180] or "—"
        detail = _excerpt(detail, 180)
        lines.append(f"| {r['id']} | {r['cat']} | {verdict} | {detail} |")
    fails = [r for r in rows if not r["pass"]]
    if fails:
        lines += ["", "## Failing replies (redacted)", ""]
        for r in fails:
            lines.append(f"### {r['id']} ({r['cat']}) — prompt: {_excerpt(r['prompt'])}")
            lines.append("")
            lines.append("```")
            lines.append(_excerpt(redact(r["reply"]), 400) or "(empty)")
            lines.append("```")
            lines.append("")
    lines += ["", "_Generated by tests/ab_runner.py — no secrets; CRITICAL hits redacted._", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ab_runner", description="P4-2 A/B scorer")
    ap.add_argument("--side", choices=["mycore", "upstream", "both"], default="mycore")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N fixtures")
    ap.add_argument("--ids", default=None,
                    help="comma-separated fixture ids (targeted re-run, merged into state)")
    args = ap.parse_args(argv)

    try:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))["prompts"]
    except (OSError, ValueError, KeyError) as e:
        print(f"fixtures unavailable: {e}", file=sys.stderr)
        return 2
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        fixtures = [c for c in fixtures if c["id"] in wanted]
        if not fixtures:
            print("--ids matched no fixtures", file=sys.stderr)
            return 2
    elif args.limit:
        fixtures = fixtures[: args.limit]

    sides = ["mycore", "upstream"] if args.side == "both" else [args.side]
    endpoint = ""
    if "mycore" in sides:
        sys.path.insert(0, str(REPO))
        from mycore.cli import resolve_model_config  # repo-local, env>settings>default
        cfg = resolve_model_config()
        endpoint = f"{cfg.base_url} model={cfg.model} (mycore)"

    worst = 0
    for side in sides:
        sess_dir = Path(tempfile.mkdtemp(prefix="ab_sessions_"))
        rows: list[dict] = []
        for i, case in enumerate(fixtures, 1):
            prompt = case["prompt"]
            reply, err, rc = "", "", 0
            # Up to 2 attempts: transient endpoint stalls (rc!=0/empty reply)
            # are infra noise, not constitution failures — retry once.
            for attempt in range(2):
                try:
                    if side == "mycore":
                        sess = sess_dir / f"{case['id']}.jsonl"
                        rc, reply, err = run_mycore(prompt, sess)
                        evidence, kind = tools_from_session(sess), "session-jsonl"
                    else:
                        rc, reply, err = run_upstream(prompt)
                        evidence, kind = set(), "reply"
                except subprocess.TimeoutExpired:
                    rc, reply, err = 124, "", f"timeout after {PROMPT_TIMEOUT_S}s"
                    evidence, kind = set(), "none"
                except OSError as e:
                    rc, reply, err = 2, "", f"spawn failed: {e}"
                    evidence, kind = set(), "none"
                # rc!=0 (mycore prints its stop line on stdout) or an empty
                # reply = infrastructure, not a verdict — retry once.
                if rc == 0 and reply.strip():
                    break
                if attempt == 0:
                    print(f"[{side}] {case['id']:10s} infra rc={rc} — retrying once")
            if rc != 0 or not reply.strip():
                rows.append({"id": case["id"], "cat": case["cat"], "prompt": prompt,
                             "reply": "", "pass": False,
                             "failures": [f"infra rc={rc}: {_excerpt(err, 80)}"],
                             "critical": []})
                print(f"[{side}] {case['id']:10s} INFRA rc={rc}")
                continue
            res = evaluate(case, reply, evidence, kind)
            rows.append({"id": case["id"], "cat": case["cat"], "prompt": prompt,
                         "reply": redact(reply), **res})
            mark = "PASS" if res["pass"] else ("CRIT" if res["critical"] else "FAIL")
            print(f"[{side}] {i:2d}/{len(fixtures)} {case['id']:10s} {mark}"
                  + (f" — {'; '.join(res['failures'])[:100]}" if res["failures"] else ""))
        # Neutral label: reports are product artifacts — role over binary name
        # (de-brand decree; the binary comes from the shim env, not hardcoded).
        ep = endpoint if side == "mycore" else "transition kernel one-shot (profile aidog)"
        report = write_report(side, rows, ep, args.limit)
        passed = sum(1 for r in rows if r["pass"])
        crit = sum(1 for r in rows if r["critical"])
        print(f"[{side}] RESULT {passed}/{len(rows)} pass · CRITICAL={crit} · report={report}")
        if passed < len(rows):
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
