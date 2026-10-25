"""Smoke test: the package and every subpackage import."""

import importlib

import pytest

SUBPACKAGES = ["data", "features", "models", "portfolio", "research", "reports"]


def test_import_package() -> None:
    import tiferet

    assert tiferet.__doc__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_import_subpackage(name: str) -> None:
    module = importlib.import_module(f"tiferet.{name}")
    assert module.__doc__
