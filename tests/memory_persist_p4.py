#!/usr/bin/env python3
"""P4 cross-machine memory portability test — deploy/aidog_persist.py contract.

Offline (no network), stdlib only; every write is confined to /tmp.
Exit 0 = all assertions passed.

Contracts locked here (regression wall):
  - export roundtrip: fake source in /tmp -> tarball + sidecar -> import into a
    tmp dest-profile + tmp source-home -> files restored byte-identical,
    manifest per-file sha256 verifies, manifest carries no absolute machine paths
  - credential gate: secret-shaped payload -> export exits 3, writes NO tarball,
    prints file + pattern label but never the matched value
  - tar-slip defense: '../' entries and symlink entries -> import refuses
    (exit != 0) and extracts nothing
  - missing source -> export exits 1 with a clear message, no tarball
  - deploy/memory_sources.local: written on a fresh machine, NEVER clobbered
    when it already exists (content byte-identical after import)
  - aidog_memory.py: import-safe (no import side effects), env AIDOG_MEMORY_SRC
    beats the local file, provenance string de-branded
  - P5-4 memory-home chain: env AIDOG_MEMORY_HOME > local MEMORY_HOME >
    $HOME/.aidog/memories (default, materialized by sync) > legacy shim profile
    memories (only while deploy/upstream_shim.env exists); sync creates the home
    on a fresh machine; import destinations use the same chain

All import runs target a throwaway copy of deploy/ under /tmp, so the real
profile dir and the real deploy/memory_sources.local are never touched.
English output per repo i18n policy.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent
PERSIST = REPO / "deploy" / "aidog_persist.py"
AIDOG_MEMORY = REPO / "deploy" / "aidog_memory.py"
TMP = Path("/tmp")  # hard boundary: no writes outside /tmp

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("AIDOG_MEMORY_SRC", "AIDOG_MEMORY_PERSONA",
                        "AIDOG_MEMORY_HOME", "AIDOG_SKILL_DIRS")}
    if extra:
        env.update(extra)
    return env


def run(script: Path, args: list[str], extra_env: dict | None = None) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(script), *args],
                       capture_output=True, text=True, timeout=60,
                       env=clean_env(extra_env))
    return p.returncode, p.stdout + p.stderr


def make_fake_repo(root: Path) -> Path:
    """Throwaway deploy/ trio under /tmp so imports write only inside /tmp."""
    (root / "deploy").mkdir(parents=True)
    for name in ("aidog_persist.py", "aidog_memory.py", "check_publish.py"):
        shutil.copy2(REPO / "deploy" / name, root / "deploy" / name)
    return root / "deploy" / "aidog_persist.py"


def write_fake_shim(deploy_dir: Path, profile_dir: Path) -> None:
    """Minimal transition shim for a fake repo (paths stay inside /tmp)."""
    (deploy_dir / "upstream_shim.env").write_text(
        f'export AIDOG_PROFILE_DIR="{profile_dir}"\n', encoding="utf-8")


def tar_add(tar: tarfile.TarFile, name: str, data: bytes,
            kind: int = tarfile.REGTYPE, link: str = "") -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = 0 if kind != tarfile.REGTYPE else len(data)
    info.linkname = link
    tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def test_roundtrip(t: Path) -> Path:
    """Export -> verify -> import into tmp dests. Returns the tarball path."""
    src = t / "src"
    (src / "daily").mkdir(parents=True)
    (src / "MEMORY.md").write_text("# MEMORY\nfull curated body\n", encoding="utf-8")
    (src / "daily" / "2026-10-18.md").write_text("day log entry\n", encoding="utf-8")
    (src / "SCRATCHPAD.md").write_text("- [ ] todo\n", encoding="utf-8")
    persona = t / "persona_refs"
    persona.mkdir()
    (persona / "profile.md").write_text("persona profile body\n", encoding="utf-8")
    out = t / "out"

    rc, so = run(PERSIST, ["export", "--src", str(src), "--persona", str(persona),
                           "--out", str(out), "--tag", "roundtrip"])
    check("export exit 0", rc == 0, so[:300])
    tarballs = sorted(out.glob("aidog_memory_*.tar.gz")) if out.is_dir() else []
    check("exactly one tarball created", len(tarballs) == 1, str(tarballs))
    if not tarballs:
        return Path("")
    tb = tarballs[0]
    check("filename carries tag", "_roundtrip.tar.gz" in tb.name, tb.name)

    side = Path(str(tb) + ".sha256")
    check("sidecar exists", side.is_file())
    if side.is_file():
        want = side.read_text(encoding="utf-8").split()[0]
        got = hashlib.sha256(tb.read_bytes()).hexdigest()
        check("sidecar sha256 matches tarball", want == got)

    with tarfile.open(tb) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        members = {n: tar.extractfile(n).read()  # type: ignore[union-attr]
                   for n in tar.getnames() if n != "manifest.json"}
    check("manifest format_version 1", manifest.get("format_version") == 1)
    check("manifest source_kind pi-memory", manifest.get("source_kind") == "pi-memory")
    check("manifest tag recorded", manifest.get("tag") == "roundtrip")
    check("manifest created_at is UTC ISO", str(manifest.get("created_at", "")).endswith("+00:00"))
    paths = [f["path"] for f in manifest["files"]]
    mtext = json.dumps(manifest)
    check("manifest paths relative only",
          all(not p.startswith("/") and ".." not in PurePosixPath(p).parts for p in paths)
          and "/mnt/" not in mtext and "/root/" not in mtext,
          mtext[:200])
    hashes_ok = all(
        f["path"] in members
        and len(members[f["path"]]) == f["size"]
        and hashlib.sha256(members[f["path"]]).hexdigest() == f["sha256"]
        for f in manifest["files"])
    check("manifest per-file sha256 verify", hashes_ok)
    check("totals consistent", manifest["totals"]["files"] == len(manifest["files"]))
    check("bundle prefixes memory/ + persona/",
          all(p.startswith(("memory/", "persona/")) for p in paths))

    # import into a throwaway fake repo (so writes stay inside /tmp)
    fake_script = make_fake_repo(t / "fake")
    dest, sh, ph = t / "profile", t / "source_home", t / "persona_home"
    rc, so = run(fake_script, ["import", str(tb), "--dest-profile", str(dest),
                               "--source-home", str(sh), "--persona-home", str(ph)])
    check("import exit 0", rc == 0, so[:400])
    for rel in ("MEMORY.md", "daily/2026-10-18.md", "SCRATCHPAD.md"):
        orig = (src / rel).read_bytes()
        p1 = dest / "memories" / rel
        p2 = sh / rel
        check(f"restored byte-identical (profile) {rel}",
              p1.is_file() and p1.read_bytes() == orig)
        check(f"restored byte-identical (source-home) {rel}",
              p2.is_file() and p2.read_bytes() == orig)
    check("persona restored to persona-home",
          (ph / "profile.md").is_file()
          and (ph / "profile.md").read_bytes() == (persona / "profile.md").read_bytes())
    local = t / "fake" / "deploy" / "memory_sources.local"
    check("fresh machine: memory_sources.local written with MEMORY_SRC",
          local.is_file() and f"MEMORY_SRC={sh}" in local.read_text(encoding="utf-8"))
    check("import prints budget summary", "budget" in so and "import ok" in so, so[-300:])
    return tb


def test_memory_home_chain(t: Path, tb: Path) -> None:
    """P5-4: DST/import destinations resolve through the portable 4-step chain.

    Every subprocess runs with a tmp HOME (or a fake repo whose shim points
    into /tmp), so no test can touch a real profile dir or $HOME homes.
    """
    # step 1: env wins over everything (real repo, real shim present)
    env_home = t / "chain_env_home"
    _, so = run(AIDOG_MEMORY, ["status"], {"AIDOG_MEMORY_HOME": str(env_home)})
    check("home chain: env AIDOG_MEMORY_HOME wins",
          f"home: {env_home} (source=env AIDOG_MEMORY_HOME)" in so, so[:200])

    # step 2: local MEMORY_HOME beats the legacy shim profile
    f_local = t / "chain_local"
    script_l = make_fake_repo(f_local)
    prof_l = t / "chain_local_profile"
    write_fake_shim(f_local / "deploy", prof_l)
    local_home = t / "chain_local_home"
    (f_local / "deploy" / "memory_sources.local").write_text(
        f"MEMORY_HOME={local_home}\n", encoding="utf-8")
    _, so = run(script_l.parent / "aidog_memory.py", ["status"],
                {"HOME": str(t / "chain_l_home")})
    check("home chain: local MEMORY_HOME beats legacy shim",
          f"home: {local_home} (source=local MEMORY_HOME)" in so, so[:200])

    # step 4: legacy shim profile while deploy/upstream_shim.env exists
    f_shim = t / "chain_shim"
    script_s = make_fake_repo(f_shim)
    prof_s = t / "chain_shim_profile"
    write_fake_shim(f_shim / "deploy", prof_s)
    _, so = run(script_s.parent / "aidog_memory.py", ["status"],
                {"HOME": str(t / "chain_s_home")})
    check("home chain: legacy shim profile used while shim exists",
          f"home: {prof_s / 'memories'} (source=legacy shim profile)" in so, so[:200])

    # step 3 (materialized) beats step 4: portable default already exists
    mat_home = t / "chain_mat_home"
    (mat_home / ".aidog" / "memories").mkdir(parents=True)
    _, so = run(script_s.parent / "aidog_memory.py", ["status"],
                {"HOME": str(mat_home)})
    check("home chain: materialized ~/.aidog/memories beats legacy shim",
          f"home: {mat_home / '.aidog' / 'memories'} (source=default" in so, so[:200])

    # step 3 final fallback: no shim -> post-swap default (fresh $HOME)
    f_def = t / "chain_default"
    script_d = make_fake_repo(f_def)   # no upstream_shim.env copied
    fresh = t / "chain_fresh_home"
    _, so = run(script_d.parent / "aidog_memory.py", ["status"],
                {"HOME": str(fresh)})
    check("home chain: no shim -> default ~/.aidog/memories",
          f"home: {fresh / '.aidog' / 'memories'} (source=default" in so, so[:200])

    # sync creates the default home on a fresh machine (write-path safety)
    mem_src = t / "chain_mem_src"
    mem_src.mkdir()
    (mem_src / "MEMORY.md").write_text("# MEMORY\nchain test source\n", encoding="utf-8")
    ov = f_def / "overlay" / "memory"
    ov.mkdir(parents=True)
    (ov / "MEMORY_curated.md").write_text("- curated chain memory\n", encoding="utf-8")
    (ov / "USER_curated.md").write_text("- curated chain user\n", encoding="utf-8")
    (f_def / "deploy" / "memory_sources.local").write_text(
        f"MEMORY_SRC={mem_src}\n", encoding="utf-8")
    rc, so = run(script_d.parent / "aidog_memory.py", ["sync"],
                 {"HOME": str(fresh)})
    check("sync: exit 0 on a fresh machine (home auto-created)", rc == 0, so[:300])
    check("sync: default home materialized with MEMORY.md",
          (fresh / ".aidog" / "memories" / "MEMORY.md").is_file(),
          str(fresh / ".aidog"))
    check("sync: home line reports the default chain step",
          f"home: {fresh / '.aidog' / 'memories'} (source=default" in so, so[:200])

    # import destinations (no --dest-profile) follow the same chain
    f_imp = t / "chain_imp"
    script_i = make_fake_repo(f_imp)
    prof_i = t / "chain_imp_profile"
    write_fake_shim(f_imp / "deploy", prof_i)
    rc, so = run(script_i, ["import", str(tb),
                            "--source-home", str(t / "chain_imp_src"),
                            "--persona-home", str(t / "chain_imp_per")],
                 {"HOME": str(t / "chain_imp_home")})
    check("import dest chain: legacy shim memories used without --dest-profile",
          rc == 0 and (prof_i / "memories" / "MEMORY.md").is_file()
          and "home=legacy shim profile" in so, so[:400])

    # real repo (today: shim present) — status must resolve a home and label it
    _, so = run(AIDOG_MEMORY, ["status"], {})
    shim = REPO / "deploy" / "upstream_shim.env"
    home_ok = False
    if shim.is_file():
        m = re.search(r'(?m)^export AIDOG_PROFILE_DIR="([^"]+)"$',
                      shim.read_text(encoding="utf-8"))
        if m:
            home_ok = f"home: {Path(m.group(1)) / 'memories'} "                       "(source=legacy shim profile)" in so
    if not home_ok:
        home_ok = f"home: {os.path.expanduser('~/.aidog/memories')} (source=default" in so
    check("home chain: real repo resolves + labels its home", home_ok, so[:200])


def test_credential_gate(t: Path) -> None:
    bad_src = t / "bad_src"
    bad_src.mkdir()
    (bad_src / "MEMORY.md").write_text("# clean\n", encoding="utf-8")
    secret = "GITHUB_" + "PAT" + "=" + "abcdefghijklmnop123456"
    (bad_src / "creds.txt").write_text(secret + "\n", encoding="utf-8")
    out = t / "out_gate"
    rc, so = run(PERSIST, ["export", "--src", str(bad_src), "--out", str(out)])
    check("credential gate exit 3", rc == 3, f"rc={rc} {so[:200]}")
    check("no tarball written on refusal",
          not out.exists() or not list(out.glob("*.tar.gz")))
    check("gate prints file + pattern label",
          "REFUSED" in so and "filled-credential" in so and "creds.txt" in so, so[:300])
    check("gate never echoes the matched value", "abcdefghijklmnop123456" not in so)


def test_tar_slip(t: Path) -> None:
    manifest = {
        "format_version": 1,
        "files": [{"path": "memory/x.md", "size": 4,
                   "sha256": hashlib.sha256(b"pwn\n").hexdigest()}],
    }
    mbytes = json.dumps(manifest).encode("utf-8")

    evil = t / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        tar_add(tar, "manifest.json", mbytes)
        tar_add(tar, "memory/x.md", b"pwn\n")
        tar_add(tar, "../evil", b"pwned\n")
    d, s, p = t / "d3", t / "s3", t / "p3"
    rc, so = run(PERSIST, ["import", str(evil), "--dest-profile", str(d),
                           "--source-home", str(s), "--persona-home", str(p)])
    check("tar-slip '../' refused (rc != 0)", rc != 0, f"rc={rc} {so[:200]}")
    check("tar-slip: nothing extracted at all",
          not d.exists() and not s.exists() and not p.exists(), so[:200])
    check("tar-slip: no file escaped the dest",
          not (d.parent / "evil").exists() and not (t / "evil").exists()
          and not (s.parent / "evil").exists())
    check("tar-slip message says refused", "failed" in so or "unsafe" in so, so[:200])

    link = t / "link.tar.gz"
    with tarfile.open(link, "w:gz") as tar:
        tar_add(tar, "manifest.json", mbytes)
        tar_add(tar, "memory/link", b"", kind=tarfile.SYMTYPE, link="/tmp/pwn")
    d2, s2, p2 = t / "d4", t / "s4", t / "p4"
    rc, so = run(PERSIST, ["import", str(link), "--dest-profile", str(d2),
                           "--source-home", str(s2), "--persona-home", str(p2)])
    check("symlink entry refused (rc != 0)", rc != 0, f"rc={rc} {so[:200]}")
    check("symlink: nothing extracted", not d2.exists() and not s2.exists())


def test_missing_source(t: Path) -> None:
    out = t / "out_missing"
    rc, so = run(PERSIST, ["export", "--src", str(t / "no_such_source_xyz"),
                           "--out", str(out)])
    check("missing source exit 1", rc == 1, f"rc={rc}")
    check("missing source prints clear message", "not found" in so, so[:200])
    check("missing source writes no tarball",
          not out.exists() or not list(out.glob("*.tar.gz")))

    rc, so = run(PERSIST, ["export", "--out", str(out)])  # chain falls back to local/profile
    # Depends on machine state (local config / profile snapshot / gate hits):
    # only assert a clean exit code, never a crash.
    check("bare export exits cleanly (0/1/3)", rc in (0, 1, 3), f"rc={rc} {so[:200]}")


def test_local_not_clobbered(t: Path, tb: Path) -> None:
    fake = t / "fake2"
    script = make_fake_repo(fake)
    local = fake / "deploy" / "memory_sources.local"
    sentinel = "# machine config sentinel\nMEMORY_SRC=/sentinel/old/source\n"
    local.write_text(sentinel, encoding="utf-8")
    d, s, p = t / "d5", t / "s5", t / "p5"
    rc, so = run(script, ["import", str(tb), "--dest-profile", str(d),
                          "--source-home", str(s), "--persona-home", str(p)])
    check("import with existing local exit 0", rc == 0, so[:300])
    check("existing memory_sources.local NOT overwritten",
          local.read_text(encoding="utf-8") == sentinel)
    check("import reports kept config",
          "exists" in so and "kept" in so and "memory_sources.local" in so, so[:400])
    check("kept config pointers are printed", "/sentinel/old/source" in so)


def test_list(t: Path, tb: Path) -> None:
    good_dir = t / "out"
    rc, so = run(PERSIST, ["list", "--dir", str(good_dir)])
    check("list healthy dir exit 0 + OK line", rc == 0 and "OK" in so, so[:300])

    bad_dir = t / "list_bad"
    bad_dir.mkdir()
    shutil.copy2(tb, bad_dir / tb.name)
    Path(str(bad_dir / tb.name) + ".sha256").write_text(
        "0" * 64 + f"  {tb.name}\n", encoding="utf-8")
    rc, so = run(PERSIST, ["list", "--dir", str(bad_dir)])
    check("list corrupted sidecar exit 1 + BAD line", rc == 1 and "BAD" in so, so[:300])

    rc, so = run(PERSIST, ["list", "--dir", str(t / "no_such_dir")])
    check("list missing dir exits 0 with message", rc == 0 and "no bundles" in so)


def test_aidog_memory_contract() -> None:
    # import-safety: exactly one line of output (the SRC echo), no side effects
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import aidog_memory; "
            "print(aidog_memory.SRC or 'unset')")
    p = subprocess.run([sys.executable, "-c", code, str(REPO / "deploy")],
                       capture_output=True, text=True, timeout=30, env=clean_env())
    check("aidog_memory import-safe (rc 0, single-line output)",
          p.returncode == 0 and len(p.stdout.strip().splitlines()) == 1,
          p.stdout + p.stderr)

    env_win = "/tmp/env_memory_src_wins"
    p = subprocess.run([sys.executable, "-c", code, str(REPO / "deploy")],
                       capture_output=True, text=True, timeout=30,
                       env=clean_env({"AIDOG_MEMORY_SRC": env_win}))
    check("env AIDOG_MEMORY_SRC beats memory_sources.local",
          p.returncode == 0 and env_win in p.stdout, p.stdout + p.stderr)

    src_text = AIDOG_MEMORY.read_text(encoding="utf-8")
    check("provenance de-branded (no project name)", "chain-c" not in src_text)
    check("provenance new wording present",
          "pi-side project memory (authoritative, one-way snapshot)" in src_text)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aidog_p4_", dir=str(TMP)) as td:
        t = Path(td)
        tb = test_roundtrip(t)
        if not tb.is_file():
            print("roundtrip export failed — aborting remaining bundle tests")
            return 1
        test_memory_home_chain(t, tb)
        test_credential_gate(t)
        test_tar_slip(t)
        test_missing_source(t)
        test_local_not_clobbered(t, tb)
        test_list(t, tb)
    test_aidog_memory_contract()

    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
