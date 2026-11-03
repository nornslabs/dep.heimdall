"""Collects every module's table models into one SQLAlchemy MetaData for alembic and tests."""

import importlib
import importlib.util
import pkgutil

from sqlmodel import SQLModel

import heimdall.modules

metadata = SQLModel.metadata
"""The MetaData every table model registers with."""

QUEUE_TABLE_PREFIX = "procrastinate_"
"""procrastinate owns these tables; its migration creates them and no model describes them."""

SHARED_TABLE_MODULES = ("heimdall.adapters.db.outbox.models",)
"""Tables written by every module, so they live in the adapter instead of one module."""


def import_all_models() -> list[str]:
    """Import every table model so it registers on ``metadata``.

    That is each feature module's ``models.py`` plus the shared tables in
    ``SHARED_TABLE_MODULES``.

    Returns:
        Dotted names of the feature modules' ``models`` that were imported, in sorted order.
    """
    for dotted in SHARED_TABLE_MODULES:
        importlib.import_module(dotted)
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


def include_in_migrations(name: str | None, type_: str, _parent_names: object) -> bool:
    """Tell alembic which database objects our models describe.

    procrastinate's tables are left out, so autogenerate and the drift test leave them alone.

    Args:
        name: Name of the reflected object.
        type_: Object kind, for example ``"table"`` or ``"index"``.

    Returns:
        False for procrastinate's tables, True for everything else.
    """
    return not (type_ == "table" and name is not None and name.startswith(QUEUE_TABLE_PREFIX))
