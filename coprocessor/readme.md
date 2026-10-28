# AMT Digital Testbed Platform (DTP) Isaac Sim Co-Processor (ISCP)

The AMT DTP ISCP is a separate application that provides capabilities to the AMT DTP Isaac Sim Extension (ISE) which
are not possible inside Isaac Sim itself: many Python packages (mesh processing with `trimesh`, `manifold3d`, `scipy`,
`scikit-image`, and optionally STEP import) conflict with the packages in Isaac Sim's Python environment, so they run
here, in their own virtual environment, behind a web API. The `AMT DTP Isaac Sim Python Library (ISPL)` (`/lib` in this
repository) provides the API client.

The co-processor is packaged inside the AMT DTP ISE (`/extension` in this repository), which installs, launches and
manages it. When the ISE is installed and running in Isaac Sim, the co-processor is available alongside it.

## Build

The build is Linux-only. On Windows, use a Debian WSL environment:

```
wsl --install Debian
wsl --set-default Debian
wsl
```

In the Debian WSL environment or on a Debian-based Linux distribution (e.g., Ubuntu), install the build tools:

```bash
sudo apt update
sudo apt install -y make python3 python3-venv python3-pip rsync zip
```

Then build `dist/amt_dtp_iscp-<version>-py3-none-any.whl`:

```bash
make
```

The version is stamped at build time from the extension's `extension.toml`; the committed `__version__` is `0.0.0`.

## Install and run

`install/install.sh` (Linux) and `install/install.bat` (Windows) create a virtual environment next to the wheel and
install it; `install/run.sh` / `install/run.bat` start the co-processor. The extension ships these scripts with the
wheel and runs them itself.

On Windows, `install.bat` installs `manifold3d` first with `--config-settings=cmake.args="-DMANIFOLD_PAR=NONE"`, a
workaround for its wheel build; building it from source needs the Microsoft C++ build tools.

## Optional STEP import

STEP (`.step`/`.stp`) upload needs the `step` extra (`amt-dtp-iscp[step]`), which installs `cascadio`. It is opt-in
because `cascadio`'s wheel bundles OpenCASCADE (LGPL-2.1 with the OCCT exception); without it a STEP upload is answered
with 422 and a hint.

## License

Apache License 2.0. The wheel carries `LICENSE.TXT` and `NOTICE`; its dependencies are listed, with their licenses, in
`THIRD-PARTY-NOTICES.md`.
