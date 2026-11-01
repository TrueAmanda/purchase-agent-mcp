"""Backend compatibility imports for shared database initialization."""

from purchase_storage.database import build_engine, initialize_database

__all__ = ["build_engine", "initialize_database"]
