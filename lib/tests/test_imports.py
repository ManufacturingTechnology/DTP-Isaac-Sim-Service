import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "amt.dtp.ise.lib.apiclient",
    "amt.dtp.ise.lib.coprocessorclient",
    "amt.dtp.ise.lib.models",
    "amt.dtp.ise.lib.logging",
    "amt.dtp.ise.lib.control.component",
    "amt.dtp.ise.lib.control.codegen",
    "amt.dtp.ise.lib.control.core.models",
    "amt.dtp.ise.lib.control.core.client",
    "amt.dtp.ise.lib.control.transducer.models",
    "amt.dtp.ise.lib.control.segment.models",
    "amt.dtp.ise.lib.control.segment.engine",
    "amt.dtp.ise.lib.control.segment.client",
    "amt.dtp.ise.lib.devices.spacemouse",
    "amt.dtp.ise.lib.devices.spacemouse.hid",
    "amt.dtp.ise.lib.devices.spacemouse.linux",
])
def test_import(module):
    importlib.import_module(module)


def test_client_does_not_need_server_dependencies():
    # The client must work without the server extra (fastapi, uvicorn).
    code = (
        "import sys; import amt.dtp.ise.lib.control.core.client, amt.dtp.ise.lib.control.segment.client, "
        "amt.dtp.ise.lib.control.segment.engine; "
        "sys.exit(1 if 'fastapi' in sys.modules or 'uvicorn' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
