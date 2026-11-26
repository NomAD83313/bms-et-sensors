# Remote access through a Cloudflare Tunnel

The stack can be reached from the internet without port forwarding or a public IP:
`cloudflared` (Compose profile `tunnel`) opens an outbound connection to Cloudflare and
forwards the public hostname to the `dashboard` nginx, which already routes every UI
(`/grafana/`, `/graf/`, `/mscl/`, `/vibration/`, `/cameras/`, `/api/control/`, ...).

```
browser --https--> Cloudflare (Access login) --tunnel--> cloudflared --> dashboard:80 --> services
```

InfluxDB (`:8086`) and the Matter server (`:5580`) have no nginx route and stay LAN-only.

## Security model

The stack itself has no login, and its UIs can restart services and write sensor
settings. Cloudflare Access is therefore mandatory:

- nginx returns `403` for every request that came through Cloudflare (`Cf-Connecting-Ip`)
  without a Cloudflare Access token (`Cf-Access-Jwt-Assertion`). If the Access
  application is missing or misconfigured, the site stays closed instead of open.
- LAN access (`http://<host>/`) is unchanged.
- Never publish InfluxDB, Grafana or any service port directly in the tunnel.

## Setup

Use one remotely managed tunnel per host and a hostname in a zone managed by Cloudflare.
On the new host with Docker Compose installed, pass its public hostname to the setup
script. For example:

```bash
./scripts/setup-tunnel.sh ets2.douzhyk.net
```

The script prints the Cloudflare setup steps and creates a private `.env` from
`.env.example` if needed. Create the tunnel in Cloudflare and copy the value after
`--token` from the Docker install command into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
Rerun the same command to start `cloudflared` through Compose.

In Cloudflare Zero Trust, create a self-hosted Access application for the hostname with
an empty path and an **Allow** policy for the authorized emails. **One-time PIN** can
serve as the login method. On the tunnel, add the **Published application** route for
the same hostname with service URL `http://dashboard:80`. Cloudflare creates the DNS
record when you add the route. Under **SSL/TLS → Edge Certificates**, enable **Always
Use HTTPS**. Open the public HTTPS URL and sign in to verify the dashboard.

## Operation

- Restart after updating the token: `./scripts/restart-tunnel.sh`
- Connector logs: `./scripts/logs.sh cloudflared`
- Stop: `docker compose -f docker-compose.yml -f docker-compose.override.yml --profile tunnel stop cloudflared`
- `./scripts/update-from-github.sh` restarts the tunnel only if it was running.
- A registered connection confirms the connector can reach Cloudflare. If Cloudflare
  Access opens but the origin returns `503`, check that the published application
  route is configured on the same tunnel as the connector token.
- `403 Forbidden` from nginx through the domain means the request had no Access token:
  check that the Access application covers exactly this hostname.
- Grafana keeps its own login (`GRAFANA_ADMIN_PASSWORD`) on top of Access.
