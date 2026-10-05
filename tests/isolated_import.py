"""Load a fresh application module without leaking fake dependencies."""

from importlib.util import find_spec, module_from_spec
from unittest.mock import patch
import sys


def load_isolated_module(name, dependencies):
    spec = find_spec(name)
    module = module_from_spec(spec)
    with patch.dict(sys.modules, {**dependencies, name: module}):
        spec.loader.exec_module(module)
    return module
