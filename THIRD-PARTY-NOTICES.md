# Third-party notices

HoneyMOM, Copyright (C) 2026 蜂元科技（郑州）有限责任公司 (HoneyMeta Technology (Zhengzhou) Co., Ltd.), is licensed under AGPL-3.0-or-later (see [LICENSE](LICENSE)). It builds on the open-source projects listed
below. Each component is licensed under its own terms; the license texts are included in the respective packages
(Python wheels, npm packages) and, for the Docker image, in the installed packages. This list covers direct
dependencies and the components bundled into the web UI; transitive dependencies are listed in the package metadata
(`pip show`, `web/package-lock.json`).

## Acknowledgement: ktg-mes

The way HoneyMOM divides its features into modules, and the data structures of those modules, were designed with
reference to the module division of the open-source MES **ktg-mes** (https://gitee.com/kutangguo/ktg-mes).
HoneyMOM's source code and user interface are written independently; no ktg-mes source code is included. We thank the ktg-mes authors. ktg-mes is distributed under the
MIT License:

```
The MIT License (MIT)

Copyright (c) 2018 ktg

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## Python runtime dependencies

From `requirements.txt` and `replayer/requirements.txt`.

| Package | Used for | License |
|---|---|---|
| FastAPI | web framework | MIT |
| Starlette (via FastAPI) | ASGI toolkit, static files | BSD-3-Clause |
| Pydantic (via FastAPI) | request validation | MIT |
| Uvicorn (`uvicorn[standard]`) | ASGI server | BSD-3-Clause |
| SQLAlchemy | database access (SQLite, PostgreSQL) | MIT |
| psycopg 3 (`psycopg[binary]`) | PostgreSQL driver | LGPL-3.0-only; the binary wheel bundles libpq (PostgreSQL License) |
| NumPy | numerics | BSD-3-Clause |
| SciPy | statistics, linear programming (charge optimization, SPC, MSA) | BSD-3-Clause |
| amqtt | MQTT client and the replayer's embedded broker | MIT |
| asyncua (opcua-asyncio) | OPC UA client and the replayer's OPC UA server | LGPL-3.0-or-later |
| pymodbus | Modbus TCP client and the replayer's Modbus server | BSD-3-Clause |
| openpyxl | Excel import/export, point tables | MIT |
| PyMuPDF (`pymupdf`) | label PDF rendering | AGPL-3.0, or a commercial license from Artifex |
| python-docx | Word export | MIT |
| Pillow | image handling | MIT-CMU (HPND) |

psycopg and asyncua are used unmodified as separately installed libraries, as the LGPL permits. If you distribute
HoneyMOM, including under a commercial license, keep them replaceable (as installed packages) and include their
license texts.

PyMuPDF is licensed under the AGPL-3.0, which is compatible with HoneyMOM's AGPL-3.0. A distribution of HoneyMOM
under the HoneyMOM commercial license that includes PyMuPDF also needs a commercial PyMuPDF license from Artifex,
or label PDF rendering must be switched off or replaced.

## Web UI dependencies

From `web/package.json`; bundled into `web/dist` by the build.

| Package | Used for | License |
|---|---|---|
| Vue | UI framework | MIT |
| Vue Router | routing | MIT |
| Vue I18n | translations | MIT |
| Element Plus, @element-plus/icons-vue | UI components, icons | MIT |
| Apache ECharts | charts | Apache-2.0 |
| three.js | 3D plant view | MIT |
| Vue Flow (`@vue-flow/core`, `background`, `controls`, `minimap`) | agent flow editor | MIT |
| ZXing (`web/public/vendor/`) | barcode and QR code scanning in the browser | Apache-2.0 |

Build tools (not part of the shipped UI): Vite (MIT), @vitejs/plugin-vue (MIT), TypeScript (Apache-2.0).

## Docker images

| Image | Used for | License |
|---|---|---|
| `python:3.12-slim-trixie` | runtime base of the HoneyMOM image (Debian packages under their own licenses; Python under the PSF License) | various |
| `node:22-bookworm-slim` | build stage for the web UI only, not part of the final image | various |
| `postgres:17` | database service in `docker-compose.yml` | PostgreSQL License |

Debian packages added to the runtime image: `postgresql-client` (PostgreSQL License), `tzdata` (public domain),
`tini` (MIT).

## Demo data and recordings

The demo plant "Northfield Castings", its products, customers, suppliers and people, and the replayer recording
`replayer/recordings/foundry-housing.jsonl.gz` are fictional. The recording was produced from a plant simulator.
They are part of HoneyMOM and licensed under AGPL-3.0-or-later.
