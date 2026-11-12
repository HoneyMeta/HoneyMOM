# Deployment

- [Docker](#docker)
- [Bare metal](#bare-metal)
- [PostgreSQL](#postgresql)
- [Reverse proxy and HTTPS](#reverse-proxy-and-https)
- [Backups](#backups)
- [Upgrades](#upgrades)

HoneyMOM runs as **one server process** per installation: it serves the API and the web UI, runs the collectors, the
agent runtime and the backup scheduler. Run one instance per database.

## Docker

The repository contains a multi-stage `Dockerfile` (Node builds `web/dist`, then a Python slim runtime) and a
`docker-compose.yml` with three services:

| Service | Image | Purpose |
|---|---|---|
| `mom` | built from the repository (`honeymom:local`) or `ghcr.io/honeymeta/honeymom` | HoneyMOM on port 8900, data in volume `mom-data` (`/data`) |
| `db` | `postgres:17` | database, data in volume `pg-data` |
| `replayer` | same image as `mom` | demo plant data over MQTT, OPC UA, Modbus TCP |

The image runs as a non-root user (uid 10001), stores everything writable under `/data` (`MOM_DATA`) and contains the
PostgreSQL 17 client tools for backups. It contains no web browser, so the PDF export of the AI daily report
(which prints through headless Chrome/Chromium, see `MOM_PDF_BROWSER`) is not available in the container; all other
PDF outputs work.

The compose file uses the `MOM_*` variable names. Older names (`MES_DB`, `HM_DATA`, `MES_LLM_*`, ...) are still
accepted by HoneyMOM, see [configuration.md](configuration.md#environment-variables).

### Demo

```bash
docker compose up            # add -d to run in the background
```

Demo accounts, password `honey123`. Do not expose a demo instance to an untrusted network.

### Production

```bash
cp docker/env.example .env
#   set POSTGRES_PASSWORD (letters and digits, or URL-encoded: it becomes part of MOM_DB), TZ, MOM_PORT, MOM_LLM_*
docker compose -f docker-compose.yml -f docker/compose.production.yml up -d
```

`docker/compose.production.yml` sets `MOM_DEMO=0`, requires `POSTGRES_PASSWORD` and leaves the replayer out (enable it
with `--profile replayer` to rehearse onboarding).

On the first start, create the administrator in the browser or with

```bash
docker compose exec mom python -m honeymom create-admin --username admin
```

Start production on new volumes. A database that was seeded in demo mode keeps its demo accounts and data; switching
`MOM_DEMO` off later does not remove them. To discard a demo installation including all data:
`docker compose down -v`.

### Using the published image

Tagged releases are published to `ghcr.io/honeymeta/honeymom` (`latest`, `X.Y.Z`, `X.Y`). To run a published image
instead of building:

```bash
# .env
HONEYMOM_IMAGE=ghcr.io/honeymeta/honeymom:1.0.0
```

```bash
docker compose pull mom replayer
docker compose up -d --no-build
```

### Useful commands

```bash
docker compose logs -f mom
docker compose exec mom python -m honeymom db status
docker compose exec mom python -m honeymom backup --note "before upgrade"
docker compose restart mom replayer        # restart the replayer together with mom (it shares mom's network)
```

### Connecting real equipment from Docker

The `mom` container reaches equipment through the Docker network. OPC UA servers, MQTT brokers and Modbus devices on
the plant network are reachable by IP address or DNS name as long as the Docker host can reach them. If a device only
accepts connections from known IPs, allow the Docker host's address. For field networks that need layer-2 access,
run the `mom` service with `network_mode: host` (Linux) in your own override file.

## Bare metal

Requirements: Python 3.12, Node.js 20+ to build the web UI, optionally PostgreSQL 16 or 17 and its client tools.

```bash
git clone https://github.com/HoneyMeta/HoneyMOM.git /opt/honeymom
cd /opt/honeymom
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd web && npm ci && npm run build)
MOM_DATA=/var/lib/honeymom MOM_DB=postgresql+psycopg://honeymom:...@localhost:5432/honeymom \
  .venv/bin/python -m honeymom serve --http 8900
```

If `web/dist` is missing the server still starts, but only the API is available.

### systemd

`/etc/systemd/system/honeymom.service`:

```ini
[Unit]
Description=HoneyMOM
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=honeymom
WorkingDirectory=/opt/honeymom
Environment=MOM_DATA=/var/lib/honeymom
EnvironmentFile=/etc/honeymom/honeymom.env
ExecStart=/opt/honeymom/.venv/bin/python -m honeymom serve --http 8900
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/honeymom/honeymom.env` (readable only by root and the service user) holds `MOM_DB`, `MOM_LLM_*` and other
variables from [configuration.md](configuration.md).

```bash
sudo useradd --system --home /var/lib/honeymom --create-home honeymom
sudo systemctl daemon-reload && sudo systemctl enable --now honeymom
```

### Windows

Install Python 3.12 and Node.js, build the web UI as above, then start with
`python -m honeymom serve` from the repository directory (set variables with `set NAME=value` first). To run it as a
service, use a service wrapper of your choice that starts this command in the repository directory.

## PostgreSQL

SQLite (the default) is fine for evaluation and small single-user setups. Use PostgreSQL for production.

```sql
CREATE USER honeymom WITH PASSWORD '...';
CREATE DATABASE honeymom OWNER honeymom;
```

```bash
MOM_DB=postgresql+psycopg://honeymom:PASSWORD@dbhost:5432/honeymom
```

- The schema is created and migrated automatically at start-up. `python -m honeymom db status`, `db upgrade
  --dry-run` and `db check` inspect it.
- **TimescaleDB (optional):** when the extension is available and the user may create it, the time-series tables
  become hypertables with continuous aggregates. Otherwise native partitioning is used. `MOM_TS_TIMESCALE=0` forces
  native partitioning. To use it with Docker, replace the `db` image with a TimescaleDB image for PostgreSQL 17 before
  the first start.
- `MOM_DB_POOL` sets the connection pool size (default 10).
- Backups need `pg_dump` of the same major version as the server (`MOM_PG_BIN` points to it if it is not on the
  usual paths).

## Reverse proxy and HTTPS

HoneyMOM serves plain HTTP. Put a reverse proxy with TLS in front of it when it is reachable beyond a trusted network.
Phones need HTTPS (or `localhost`) for camera barcode scanning in the mobile app. The AI assistant streams answers
with server-sent events, so disable response buffering for `/api/`.

nginx example:

```nginx
server {
    listen 443 ssl;
    server_name mom.example.com;
    ssl_certificate     /etc/ssl/mom.example.com.crt;
    ssl_certificate_key /etc/ssl/mom.example.com.key;
    client_max_body_size 200m;           # delivery packages, drawings, imports

    location / {
        proxy_pass http://127.0.0.1:8900;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;             # SSE for the AI assistant
        proxy_read_timeout 300s;
    }
}
```

## Backups

**System → Backup & Restore**:

- a daily scheduled backup (system parameters `backup_enabled`, `backup_time`) and manual backups;
- each backup is one zip archive in `MOM_BACKUP_DIR` (default `<MOM_DATA>/backups`) with the database (SQLite online
  backup, or `pg_dump` for PostgreSQL) and the uploaded files;
- restore drills restore a backup into a scratch location and compare it with the source automatically.

From the command line:

```bash
python -m honeymom backup --note "before upgrade"            # prints the archive path
python -m honeymom restore backups/<archive>.zip --to /var/lib/honeymom-restored \
       [--to-db postgresql+psycopg://honeymom:...@dbhost:5432/honeymom_restored]
```

`restore` never overwrites the current data: it restores into a new data directory (and, for PostgreSQL, a new
database) and verifies the result. To switch over, stop HoneyMOM, point `MOM_DATA` / `MOM_DB` at the restored copy and
start it again.

Copy the backup directory to another machine regularly. With Docker, backups are in the `mom-data` volume under
`/data/backups`:

```bash
docker compose exec mom python -m honeymom backup --note manual
docker compose cp mom:/data/backups ./honeymom-backups
```

In addition you can back up the `pg-data` volume with your usual PostgreSQL tooling.

## Upgrades

1. Read the release notes.
2. Back up (`python -m honeymom backup`).
3. Update the code or image:
   - Docker, built locally: `git pull && docker compose build && docker compose up -d`
   - Docker, published image: change `HONEYMOM_IMAGE` in `.env`, then `docker compose pull mom replayer && docker
     compose up -d --no-build`
   - bare metal: `git pull`, `pip install -r requirements.txt`, `cd web && npm ci && npm run build`
4. Optionally preview the migrations: `python -m honeymom db upgrade --dry-run`.
5. Start HoneyMOM. Pending migrations run automatically at start-up; the server refuses to start if the database and
   the schema do not match afterwards.

Downgrades are not supported; to go back, restore the backup taken before the upgrade.
