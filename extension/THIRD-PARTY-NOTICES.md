# Third-Party Notices: the extension (`amt.dtp.ise`)

The extension's own code is licensed under the Apache License 2.0 (`LICENSE.TXT`; attribution in `NOTICE`). The build
copies this file, `LICENSE.TXT`, and `NOTICE` into the extension zip.

## Included in the extension zip

| Component | Where in the zip | License |
|---|---|---|
| Eclipse Paho MQTT Python Client 2.1.0 | `pip_prebundle/paho` | Eclipse Distribution License 1.0 (chosen from its dual EPL-2.0 / EDL-1.0 licensing); text below |
| The documentation site | `docs/` | see `docs/THIRD-PARTY-NOTICES.md` in the zip (the theme and its bundled scripts) |
| The co-processor wheel and its installer scripts | `coprocessor/` | AMT, Apache-2.0; it includes no third-party code (see `coprocessor/THIRD-PARTY-NOTICES.md`) |
| The library (`amt.dtp.ise.lib`) | `amt/dtp/ise/lib/` | AMT, Apache-2.0 |

Eclipse Paho MQTT Python Client (https://github.com/eclipse-paho/paho.mqtt.python), redistributed under:

```text
Eclipse Distribution License - v 1.0

Copyright (c) 2007, Eclipse Foundation, Inc. and its licensors.

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

  Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

  Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

  Neither the name of the Eclipse Foundation, Inc. nor the names of its
  contributors may be used to endorse or promote products derived from this
  software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Referenced, not included

The extension needs these at run time; they are not in the zip. Users obtain them from their publishers, under the
publishers' terms.

| Component | How it is obtained | License |
|---|---|---|
| NVIDIA Isaac Sim 6.0.1, Omniverse Kit, and the Kit extensions listed in `config/extension.toml` (`isaacsim.*`, `omni.*`) | NVIDIA's Isaac Sim download | NVIDIA Isaac Sim Additional Software and Materials License / NVIDIA Omniverse License Agreement (Isaac Sim's source code: Apache-2.0) |
| NVIDIA cuMotion and Lula (through `isaacsim.robot_motion.*`) | part of Isaac Sim | NVIDIA proprietary |
| Python packages the extension imports from Isaac Sim's environment (e.g. FastAPI, uvicorn, pydantic, numpy, requests, OpenUSD `pxr`, Warp) | bundled with Isaac Sim | each package's own license, as distributed by NVIDIA |
| `srl.spacemouse` (the legacy SpaceMouse backend, used only if `SPACEMOUSE_BACKEND = "srl"`; the default backend is the extension's own code, `amt.dtp.ise.lib.devices.spacemouse`, which uses no third-party code) | `install-spacemouse.sh` clones https://github.com/NVlabs/spacemouse-extension and `install-spacemouse.bat` a fork of it, https://github.com/m-r-mccormick/spacemouse-extension (works with current Isaac Sim), into the user's Isaac Sim; to be replaced by the extension's own SpaceMouse support | MIT (NVIDIA). The same repository also carries an NVIDIA proprietary notice for material it took from `omni.isaac.examples`, so it is used in place and never redistributed |
| `hidapi` | installed by `srl.spacemouse` with pip at run time (legacy backend only) | GPL-3.0 OR BSD-3-Clause OR HIDAPI (its Linux wheels also bundle libusb, LGPL-2.1, and other system libraries) |
| `spacenavd` (Linux only) | installed by `install-spacemouse.sh` with apt (legacy backend only) | GPL-3.0 |
