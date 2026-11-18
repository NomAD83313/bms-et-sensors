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

One tunnel per host. A tunnel used on another host can stay as it is; create a new one
for this Pi.

1. Cloudflare dashboard -> **Zero Trust -> Networks -> Tunnels -> Create a tunnel**,
   type **Cloudflared**, name it (for example `bms-pi`).
2. On the install step choose **Docker** and copy only the token (the long value after
   `--token`). Put it into `.env` on the Pi:

   ```
   CLOUDFLARE_TUNNEL_TOKEN=eyJ...
   ```

3. **Public hostname**: subdomain `sensors` (any), your domain, service type `HTTP`,
   URL `dashboard:80`.
4. **Zero Trust -> Access -> Applications -> Add an application -> Self-hosted**:
   - application domain: the same hostname (`sensors.<domain>`), path empty;
   - policy: action **Allow**, include **Emails** (list of allowed addresses) or an
     email domain; login method **One-time PIN** works without extra setup.
5. SSL/TLS -> Edge Certificates: enable **Always Use HTTPS**.
6. Start the tunnel on the Pi:

   ```bash
   ./scripts/restart-tunnel.sh
   ./scripts/logs.sh cloudflared
   ```

7. Open `https://sensors.<domain>/`, log in with an allowed email.

## Operation

- Stop: `docker compose -f docker-compose.yml -f docker-compose.override.yml --profile tunnel stop cloudflared`
- `./scripts/update-from-github.sh` restarts the tunnel only if it was running.
- `403 Forbidden` from nginx through the domain means the request had no Access token:
  check that the Access application covers exactly this hostname.
- Grafana keeps its own login (`GRAFANA_ADMIN_PASSWORD`) on top of Access.
