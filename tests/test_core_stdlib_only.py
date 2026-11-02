"""core may import only the standard library and moneyflow.core itself.

import-linter's forbidden contract needs a list of names, so it cannot say
"every third-party package". This test is the allowlist that can.
"""
import ast
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "src" / "moneyflow" / "core"


def _imported_modules(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.append(node.module)
    return names


def _allowed(module: str) -> bool:
    return (
        module.split(".")[0] in sys.stdlib_module_names
        or module == "moneyflow.core"
        or module.startswith("moneyflow.core.")
    )


def offending_imports(root: Path) -> list[str]:
    bad = []
    for path in sorted(root.rglob("*.py")):
        for module in _imported_modules(ast.parse(path.read_text(), str(path))):
            if not _allowed(module):
                bad.append(f"{path.relative_to(root)}: {module}")
    return bad


def test_core_imports_only_stdlib_and_itself():
    assert offending_imports(CORE) == []


def test_the_check_catches_a_third_party_and_a_sibling_import(tmp_path):
    (tmp_path / "x.py").write_text(
        "import os\nimport packaging\nfrom moneyflow.cli import y\nfrom . import z\n"
    )
    assert offending_imports(tmp_path) == ["x.py: packaging", "x.py: moneyflow.cli"]
