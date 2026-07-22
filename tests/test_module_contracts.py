"""
Smoke tests: verify every registered module can be imported and exports
a callable generate(config) function with the correct signature.
"""

import importlib
import inspect
import sys
import os

import pytest

# Make project root importable when running from repo root or tests/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import MODULE_MAP


def _try_import(module_path):
    """Import a module, skipping if an optional dependency is missing."""
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        pytest.skip(f"Optional dependency missing: {exc}")


@pytest.mark.parametrize("module_name,module_path", MODULE_MAP.items())
def test_module_importable(module_name, module_path):
    """Every entry in MODULE_MAP must be importable (skips on missing optional deps)."""
    mod = _try_import(module_path)
    assert mod is not None


@pytest.mark.parametrize("module_name,module_path", MODULE_MAP.items())
def test_module_has_generate(module_name, module_path):
    """Every module must export a callable generate function."""
    mod = _try_import(module_path)
    assert hasattr(mod, "generate"), f"{module_path} missing generate()"
    assert callable(mod.generate), f"{module_path}.generate is not callable"


@pytest.mark.parametrize("module_name,module_path", MODULE_MAP.items())
def test_generate_accepts_config(module_name, module_path):
    """generate() must accept at least one positional argument (config dict)."""
    mod = _try_import(module_path)
    sig = inspect.signature(mod.generate)
    params = list(sig.parameters.values())
    assert len(params) >= 1, (
        f"{module_path}.generate() takes no arguments — expected generate(config)"
    )
