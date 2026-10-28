# Third-Party Notices: the library (`amt-dtp-ise-lib`)

The library's own code is licensed under the Apache License 2.0 (its wheel carries `LICENSE.TXT` and `NOTICE`).

## Included in the wheel

No third-party code.

## Referenced, not included

The wheel declares these dependencies; pip downloads them from their publishers. Licenses as declared by the versions
resolved on 2026-10-27; `make license-check` (repo root) re-checks them on every build.

| Package | License | Needed by |
|---|---|---|
| click | BSD-3-Clause | default |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (wheels bundle the GCC runtime libraries) | default |
| pydantic, pydantic-core, annotated-types, typing-inspection | MIT | default |
| typing-extensions | PSF-2.0 | default |
| requests | Apache-2.0 | default |
| urllib3, charset-normalizer | MIT | default |
| idna | BSD-3-Clause | default |
| certifi | MPL-2.0 | default |
| fastapi, anyio, h11, annotated-doc | MIT | `server` extra (hosting the control APIs) |
| starlette, uvicorn | BSD-3-Clause | `server` extra |
