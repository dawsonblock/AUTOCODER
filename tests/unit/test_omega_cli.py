from __future__ import annotations

from control_plane.omega_cli import _snapshot_repo_files


def test_snapshot_repo_files_captures_relative_text_files(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tmp").mkdir()
    (repo / "__pycache__").mkdir()
    (repo / "src" / "algo.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repo / "tmp" / "noise.txt").write_text("ignore\n", encoding="utf-8")
    (repo / "__pycache__" / "stale.pyc").write_bytes(b"\x00\x01")

    snapshot = _snapshot_repo_files(repo)

    assert snapshot == {
        "pyproject.toml": "[project]\nname='demo'\n",
        "src/algo.py": "print('ok')\n",
    }
