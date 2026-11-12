# Security policy

## Reporting a vulnerability

Please do not report security problems in public issues, pull requests or discussions.

Use one of these private channels:

- GitHub: **Security → Report a vulnerability** on https://github.com/HoneyMeta/HoneyMOM (private vulnerability
  reporting), or
- e-mail **dev@honeymeta.com** with the subject `SECURITY: <short description>`.

Include what you can of the following:

- the affected version or commit, and how HoneyMOM was deployed (Docker, bare metal, SQLite or PostgreSQL);
- the component (API endpoint, web page, collector protocol, MCP endpoint, replayer, Docker setup);
- steps to reproduce, a proof of concept, and the impact you expect;
- whether the problem is already public.

What happens next:

- We confirm receipt within 3 working days.
- We send a first assessment (accepted or not, severity, planned fix) within 10 working days.
- We agree a disclosure date with you. By default we publish an advisory when a fixed release is available, and no
  later than 90 days after the report. We credit you in the advisory unless you prefer not to be named.

## Supported versions

Security fixes go into the latest release and the `main` branch. Older releases are not patched; upgrade to the
latest release (see [docs/deployment.md](docs/deployment.md#upgrades)). Customers with a commercial support agreement
get fixes according to their agreement.

## Deployment notes

These are known properties of the software, not vulnerabilities. Please take them into account before you report.

- **Demo mode is not for production.** With `MOM_DEMO=1` HoneyMOM creates demo accounts that share the published
  password `honey123`. Never expose a demo instance to an untrusted network. Production installs (`MOM_DEMO` unset or
  `0`) have no default accounts; the first administrator is created on first start.
- **The replayer has no authentication.** Its MQTT broker, OPC UA server and Modbus TCP server accept anonymous
  clients and writes. It is a demo and test tool. In the provided `docker-compose.yml` its ports are not published to
  the host.
- **Industrial protocols.** Collectors connect to MQTT, OPC UA and Modbus TCP as configured in the data sources.
  Protect plant networks with network segmentation and the security features of your brokers and servers.
- **HTTPS.** HoneyMOM serves plain HTTP. Put it behind a reverse proxy with TLS when it is reachable beyond a trusted
  network (browsers also need HTTPS for camera barcode scanning on phones).
- **AI features.** When you configure a model endpoint, the AI features send prompts that contain plant data (query
  results, page context) to that endpoint. Use an endpoint you trust, or a self-hosted model. AI features are off by
  default.
- **Secrets.** Keep model API keys, database passwords and HoneyMOM API keys out of the repository. Use environment
  variables or the git-ignored `config/llm.local.json`.
