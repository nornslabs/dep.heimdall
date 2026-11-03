"""Module border rules: modules talk to each other only through ``api.py``."""

import ast
from pathlib import Path

import pytest

MODULES_ROOT = Path(__file__).resolve().parents[2] / "src" / "heimdall" / "modules"
MODULES_PKG = "heimdall.modules"


def _resolve(package: str, node: ast.ImportFrom) -> str:
    """Turn a (possibly relative) ``from`` import into an absolute dotted name."""
    if node.level == 0:
        return node.module or ""
    base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _check_target(owner: str, target: str, names: list[str]) -> list[str]:
    """Check one imported dotted target (plus imported names) against the border rules."""
    parts = target.split(".")
    if parts[:2] != MODULES_PKG.split(".") or len(parts) < 3 or parts[2] == owner:
        return []
    rest = parts[3:]
    if rest == ["api"]:
        return [f"private name {n!r} imported from {target}" for n in names if n.startswith("_")]
    if not rest and names and all(n == "api" for n in names):
        return []
    return [f"imports {'.'.join([target, *names]) if not rest else target} (only api.py is public)"]


def find_violations(package: str, source: str) -> list[str]:
    """Return border violations for a file whose dotted package is ``package``.

    ``package`` looks like ``heimdall.modules.triage`` (or deeper for subpackages).
    """
    owner = package.split(".")[2]
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            target = _resolve(package, node)
            names = [alias.name for alias in node.names]
            violations += _check_target(owner, target, names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                violations += _check_target(owner, alias.name, [])
    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from heimdall.modules.repro.service import start",
        "from heimdall.modules.repro import service",
        "import heimdall.modules.repro.domain",
        "from heimdall.modules.repro.api import _private",
        "from ..repro import service",
    ],
)
def test_flags_cross_module_import_outside_api(source: str) -> None:
    assert find_violations("heimdall.modules.triage", source), source


@pytest.mark.parametrize(
    "source",
    [
        "from heimdall.modules.repro.api import start_repro",
        "from heimdall.modules.repro import api",
        "import heimdall.modules.repro.api",
        "from heimdall.modules.triage.service import handle",
        "from .domain import State",
        "from heimdall.shared import ids",
    ],
)
def test_allows_api_imports_and_own_module(source: str) -> None:
    assert find_violations("heimdall.modules.triage", source) == []


def _package_of(path: Path) -> str:
    """Dotted package containing ``path`` (a file under ``MODULES_ROOT``)."""
    return ".".join([MODULES_PKG, *path.parent.relative_to(MODULES_ROOT).parts])


def test_real_modules_respect_borders() -> None:
    violations = [
        f"{path.relative_to(MODULES_ROOT)}: {v}"
        for path in sorted(MODULES_ROOT.glob("*/**/*.py"))
        for v in find_violations(_package_of(path), path.read_text())
    ]
    assert violations == []


def test_core_modules_do_not_import_agents_or_provider_specific_ai_clients() -> None:
    """Keep model calls in agents and provider SDKs out of core modules."""
    forbidden_prefixes = (
        "agents",
        "openai",
        "anthropic",
        "heimdall.adapters.ai",
    )
    violations: list[str] = []
    for path in sorted(MODULES_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if target.startswith(forbidden_prefixes):
                    violations.append(f"{path.relative_to(MODULES_ROOT)}: imports {target}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        violations.append(f"{path.relative_to(MODULES_ROOT)}: imports {alias.name}")
    assert violations == []
