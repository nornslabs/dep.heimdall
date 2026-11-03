"""Model access stays in agents: only agents/ and the ai package import it; no vendor SDKs."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("src", "apps", "agents", "migrations", "tests")
AI_PACKAGE = "heimdall.adapters.ai"
AI_PACKAGE_DIR = REPOSITORY_ROOT / "src/heimdall/adapters/ai"
VENDOR_PACKAGES = frozenset({"anthropic", "openai", "claude_agent_sdk", "codex"})
ALLOWED_AI_IMPORTERS = (REPOSITORY_ROOT / "agents", AI_PACKAGE_DIR, REPOSITORY_ROOT / "tests")


def _python_files() -> Iterator[Path]:
    for root in SOURCE_ROOTS:
        for path in (REPOSITORY_ROOT / root).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def _imported_modules(path: Path) -> Iterator[str]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def _is_ai_import(module: str) -> bool:
    return module == AI_PACKAGE or module.startswith(f"{AI_PACKAGE}.")


def test_ai_adapter_is_imported_only_by_agents_and_its_own_package() -> None:
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in _python_files()
        if not any(path.is_relative_to(root) for root in ALLOWED_AI_IMPORTERS)
        and any(_is_ai_import(module) for module in _imported_modules(path))
    ]

    assert offenders == []


def test_core_modules_never_import_the_ai_adapter() -> None:
    modules_root = REPOSITORY_ROOT / "src/heimdall/modules"
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in modules_root.rglob("*.py")
        if any(_is_ai_import(module) for module in _imported_modules(path))
    ]

    assert offenders == []


def test_no_vendor_llm_sdk_or_cli_is_imported_anywhere() -> None:
    offenders = [
        f"{path.relative_to(REPOSITORY_ROOT)}: {module}"
        for path in _python_files()
        for module in _imported_modules(path)
        if module.split(".")[0] in VENDOR_PACKAGES
    ]

    assert offenders == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import openai", ["openai"]),
        ("from anthropic.types import Message", ["anthropic.types"]),
        ("from heimdall.adapters.ai import AiAdapter", ["heimdall.adapters.ai"]),
    ],
)
def test_import_scanner_sees_plain_and_from_imports(
    tmp_path: Path, source: str, expected: list[str]
) -> None:
    module = tmp_path / "sample.py"
    module.write_text(source, encoding="utf-8")

    assert list(_imported_modules(module)) == expected
