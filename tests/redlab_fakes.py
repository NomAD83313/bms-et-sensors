"""Scoped ULDAQ substitutes for RedLab unit tests; no hardware is opened."""

from enum import Enum
from types import ModuleType
from unittest.mock import Mock

from tests.isolated_import import load_isolated_module


def _make_influxdb_client_fake():
    """Return the real influxdb_client if available, otherwise a minimal stub.

    The real package is needed for tests that call to_line_protocol() on Point
    objects built by build_points(); the stub is enough for tests that only
    exercise queue and retry logic without inspecting point contents.
    """
    try:
        import influxdb_client as _real  # noqa: PLC0415
        return _real
    except ImportError:
        pass

    influxdb = ModuleType("influxdb_client")

    class _Point:
        def __init__(self, *a, **kw):
            pass

        def tag(self, *a, **kw):
            return self

        def field(self, *a, **kw):
            return self

        def time(self, *a, **kw):
            return self

        def to_line_protocol(self) -> str:  # noqa: D401
            return ""

    class _WritePrecision:
        NS = "ns"

    influxdb.Point = _Point
    influxdb.WritePrecision = _WritePrecision
    return influxdb


def load_redlab_module(name="app.redlab.redlab_main"):
    uldaq = ModuleType("uldaq")
    uldaq.TcType = Enum("TcType", "B E J K N R S T")
    uldaq.TempScale = Enum("TempScale", "CELSIUS")
    uldaq.InterfaceType = Enum("InterfaceType", "USB")
    uldaq.DaqDevice = Mock()
    uldaq.get_daq_device_inventory = Mock(return_value=[])

    class ULException(Exception):
        def __init__(self, code):
            self.error_code = code
            super().__init__(str(code))

    uldaq.ULException = ULException
    config = ModuleType("uldaq.daq_device_config")
    config.DevVersionType = Enum("DevVersionType", "FW_MAIN")
    influxdb_client = _make_influxdb_client_fake()
    dependencies = {
        "uldaq": uldaq,
        "uldaq.daq_device_config": config,
        "influxdb_client": influxdb_client,
    }
    acquisition = load_isolated_module("app.redlab.redlab_acquisition", dependencies)
    dependencies.update({
        "app.redlab.redlab_acquisition": acquisition,
        "redlab_acquisition": acquisition,
    })
    from app.redlab import redlab_inventory
    dependencies["redlab_inventory"] = redlab_inventory
    for component in ("redlab_writer", "redlab_runtime", "redlab_registry", "redlab_routes"):
        full_name = "app.redlab." + component
        module = load_isolated_module(full_name, dependencies)
        dependencies[full_name] = module
        dependencies[component] = module
        if name == full_name:
            return module
    return load_isolated_module(name, dependencies)
