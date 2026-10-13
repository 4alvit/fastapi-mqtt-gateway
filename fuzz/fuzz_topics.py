import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

import atheris

# Typeshed does not yet re-export the instrumentation APIs at the package root.
if TYPE_CHECKING:
    from atheris.import_hook import instrument_imports
    from atheris.instrument_bytecode import instrument_func
else:
    instrument_imports = atheris.instrument_imports
    instrument_func = atheris.instrument_func

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("fastapi_mqtt_gateway")
package.__path__ = [str(ROOT / "src/fastapi_mqtt_gateway")]
sys.modules[package.__name__] = package
package = types.ModuleType("fastapi_mqtt_gateway.core")
package.__path__ = [str(ROOT / "src/fastapi_mqtt_gateway/core")]
sys.modules[package.__name__] = package

with instrument_imports():
    from fastapi_mqtt_gateway.core.topics import filter_contains, filters_overlap, validate_topic


def test_one_input(data: bytes) -> None:
    from fastapi import HTTPException

    left, _, right = data.decode("utf-8", errors="replace").partition("\n")
    for topic in (left, right):
        try:
            validate_topic(topic, subscription=True)
        except HTTPException:
            return
    assert filter_contains(left, left)  # nosec B101 - intentional test oracle
    assert filter_contains(right, right)  # nosec B101 - intentional test oracle
    assert filters_overlap(left, right) == filters_overlap(right, left)  # nosec B101 - intentional test oracle
    if filter_contains(left, right):
        assert filters_overlap(left, right)  # nosec B101 - intentional test oracle


if __name__ == "__main__":
    atheris.Setup(sys.argv, instrument_func(test_one_input))
    atheris.Fuzz()
