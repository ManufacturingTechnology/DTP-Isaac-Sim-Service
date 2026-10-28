# AMT Digital Testbed Platform (DTP) Isaac Sim Python Library (ISPL)

`amt-dtp-ise-lib` is the pure-Python part of the AMT DTP Isaac Sim Service: it has no Isaac Sim dependency, so it
runs in any Python 3.10+ environment, inside or outside Isaac Sim. The Isaac Sim extension (`extension/` in this
repository) and the co-processor (`coprocessor/`) both embed a copy of it; client programs install it on their own.

It provides:

- **Models** for assets, environments, workpieces and the service configuration (`amt.dtp.ise.lib.models`), plus
  environment packaging and validation (`environment`, `validation`, `package_cleanup`).
- **Control APIs** (`amt.dtp.ise.lib.control`): the server base every API is built on (`host`), automation
  components, the core and segment APIs with their clients, frame merging, the robot API with pose models and
  trajectory compiling, the kinematics service, and the generator that emits typed Python clients from an API's
  schema (`codegen`).
- **API clients** for the service and the co-processor (`apiclient`, `coprocessorclient`), geometry helpers and
  logging.

Online documentation: [https://iss.amt.onl/](https://iss.amt.onl/).

## Installation

Wheels are attached to the releases of the
[DTP-Isaac-Sim-Service](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/) repository:

```bash
pip install amt_dtp_ise_lib-<version>-py3-none-any.whl            # clients
pip install "amt_dtp_ise_lib-<version>-py3-none-any.whl[server]"  # hosting the control APIs (FastAPI, uvicorn)
```

For development, from this directory:

```bash
pip install -e ".[dev]"
```

## Build and test

`make` (Linux, or WSL on Windows) builds `dist/amt_dtp_ise_lib-<version>-py3-none-any.whl`; `make test` installs the
wheel with the `server` extra into a throwaway environment and runs `tests/`. On Windows, `test.bat` runs the tests.
The version is stamped at build time from the extension's `extension.toml`; the committed `__version__` is `0.0.0`.

## License

Apache License 2.0. The wheel carries `LICENSE.TXT` and `NOTICE`; the packages it depends on are listed, with their
licenses, in `THIRD-PARTY-NOTICES.md`.
