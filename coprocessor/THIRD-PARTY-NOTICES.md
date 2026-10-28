# Third-Party Notices: the co-processor (`amt-dtp-iscp`)

The co-processor's own code is licensed under the Apache License 2.0 (its wheel carries `LICENSE.TXT` and `NOTICE`).

## Included in the wheel

No third-party code: the wheel contains the co-processor's code and a copy of the AMT library (`amt.dtp.ise.lib`),
both Apache-2.0.

## Referenced, not included

The wheel declares these dependencies; the installer (`install/install.*`) has pip download them from their
publishers. Licenses as declared by the versions resolved on 2026-10-27; `make license-check` (repo root) re-checks
them on every build.

| Package | License | Package | License |
|---|---|---|---|
| annotated-doc | MIT | pillow | MIT-CMU |
| annotated-types | MIT | pydantic, pydantic-core | MIT |
| anyio | MIT | python-multipart | Apache-2.0 |
| certifi | MPL-2.0 | requests | Apache-2.0 |
| charset-normalizer | MIT | scikit-image | BSD-3-Clause |
| click | BSD-3-Clause | scipy | BSD-3-Clause (wheels bundle the GCC runtime libraries) |
| fastapi | MIT | starlette | BSD-3-Clause |
| h11 | MIT | tifffile | BSD-3-Clause |
| idna | BSD-3-Clause | trimesh | MIT |
| imageio | BSD-2-Clause | typing-extensions | PSF-2.0 |
| lazy-loader | BSD-3-Clause | typing-inspection | MIT |
| manifold3d | Apache-2.0 | urllib3 | MIT |
| networkx | BSD-3-Clause | uvicorn | BSD-3-Clause |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (wheels bundle the GCC runtime libraries) | packaging | Apache-2.0 OR BSD-2-Clause |

Optional, only with the `step` extra (`amt-dtp-iscp[step]`, STEP file import): **cascadio** (MIT), whose wheel
bundles **OpenCASCADE Technology** (LGPL-2.1 with the OCCT exception). Not installed by default.
