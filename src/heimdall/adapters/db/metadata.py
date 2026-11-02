"""Collects every module's table models into one SQLAlchemy MetaData for alembic and tests."""

import importlib
import importlib.util
import pkgutil

from sqlmodel import SQLModel

import heimdall.modules

metadata = SQLModel.metadata
"""The MetaData every table model registers with."""


def import_all_models() -> list[str]:
    """Import each module's ``models.py`` so its tables register on ``metadata``.

    Returns:
        Dotted names of the models modules that were imported, in sorted order.
    """
    imported: list[str] = []
    for module_info in sorted(pkgutil.iter_modules(heimdall.modules.__path__), key=_name):
        if not module_info.ispkg:
            continue
        dotted = f"heimdall.modules.{module_info.name}.models"
        if importlib.util.find_spec(dotted) is None:
            continue
        importlib.import_module(dotted)
        imported.append(dotted)
    return imported


def _name(module_info: pkgutil.ModuleInfo) -> str:
    return module_info.name
