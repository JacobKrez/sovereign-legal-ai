"""The gate scans published files, not whatever the reader creates.

Regression for a gate that went red when a reviewer made a virtual
environment under any name other than ``.venv`` or ``venv``: third-party
packages contain key-shaped strings, legal and medical vocabulary, em
dashes, and pricing language, and the old scanner walked the whole tree.

This file is scanned by the gate it tests, so the planted literals are
assembled from fragments at runtime. Only the content gates run here;
G10 runs pytest, and running it from inside pytest would recurse.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_GATES = ("G4", "G5", "G6", "G7", "G9")


def _leak() -> str:
    return (
        "api" + "_key = '" + "abcdefghijklmnopqrstuvwxyz012345" + "'\n"
        "# the Plain" + "tiff's medical " + "bills\n"
        "HOME = '" + "/Users/" + "jacob" + "/x'\n"
        "# priced per " + "seat, a seam" + "less fit " + chr(0x2014) + " yes\n"
    )


def _copy(tmp_path: Path, keep_git: bool) -> Path:
    repo = tmp_path / "repo"
    if keep_git:
        shutil.copytree(REPO_ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    else:
        _source_archive(repo)
    return repo


def _source_archive(destination: Path) -> None:
    """What a downloaded source archive holds: the tracked files, no .git.

    Copying the whole working tree would also copy whatever the person
    running the tests has lying around untracked, which no real archive
    contains.
    """
    if not (REPO_ROOT / ".git").exists():
        shutil.copytree(
            REPO_ROOT, destination, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
        )
        return
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    for name in filter(None, listed.split("\0")):
        source = REPO_ROOT / name
        if source.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _plant(repo: Path, with_pyvenv_cfg: bool) -> None:
    lib = repo / "junk_venv" / "lib"
    lib.mkdir(parents=True)
    if with_pyvenv_cfg:
        (repo / "junk_venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (lib / "pkg.py").write_text(_leak(), encoding="utf-8")


def _gate(repo: Path, gate_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/gate.py", "--quiet", "--only", gate_id],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("gate_id", CONTENT_GATES)
def test_untracked_directory_in_git_checkout_is_ignored(tmp_path: Path, gate_id: str) -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    repo = _copy(tmp_path, keep_git=True)
    _plant(repo, with_pyvenv_cfg=False)
    proc = _gate(repo, gate_id)
    assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize("gate_id", CONTENT_GATES)
def test_virtual_environment_in_source_archive_is_ignored(tmp_path: Path, gate_id: str) -> None:
    repo = _copy(tmp_path, keep_git=False)
    _plant(repo, with_pyvenv_cfg=True)
    proc = _gate(repo, gate_id)
    assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize("gate_id", CONTENT_GATES)
def test_tracked_leak_is_still_caught(tmp_path: Path, gate_id: str) -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    repo = _copy(tmp_path, keep_git=True)
    (repo / "notes.py").write_text(_leak(), encoding="utf-8")
    subprocess.run(["git", "add", "notes.py"], cwd=repo, check=True)
    proc = _gate(repo, gate_id)
    assert proc.returncode == 1, proc.stdout
