import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONFIG = PROJECT_ROOT / "dashboard" / "nginx" / "default.conf"
DASH_CONFIG = PROJECT_ROOT / "dashboard" / "simple-dash" / "config.json"
COMPOSE = PROJECT_ROOT / "docker-compose.yml"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# Services without an nginx route; they stay LAN-only and keep host:port links.
LAN_ONLY_PORTS = (":8086", ":5580")


class RemoteAccessTests(unittest.TestCase):
    def test_nginx_rejects_cloudflare_requests_without_access_token(self) -> None:
        config = NGINX_CONFIG.read_text(encoding="utf-8")
        self.assertIn("if ($http_cf_connecting_ip != \"\")", config)
        self.assertIn("if ($http_cf_access_jwt_assertion != \"\")", config)
        self.assertIn("return 403;", config)
        self.assertIn("absolute_redirect off;", config)

    def test_dashboard_links_are_relative_for_proxied_services(self) -> None:
        config = json.loads(DASH_CONFIG.read_text(encoding="utf-8"))
        links = [item["link"] for group in config["groups"] for item in group["items"]]
        for link in links:
            if link.startswith("http"):
                self.assertTrue(link.endswith(LAN_ONLY_PORTS) or any(p + "/" in link for p in LAN_ONLY_PORTS), link)
            else:
                self.assertTrue(link.startswith("/"), link)

    def test_tunnel_is_profile_gated_and_token_is_not_tracked(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        block = compose.split("  cloudflared:", 1)[1].split("\n\n", 1)[0]
        self.assertIn('profiles: ["tunnel"]', block)
        self.assertIn("TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN:-}", block)
        self.assertNotIn("ports:", block)
        self.assertIn("CLOUDFLARE_TUNNEL_TOKEN=\n", ENV_EXAMPLE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
