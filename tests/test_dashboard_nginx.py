import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONFIG = PROJECT_ROOT / "dashboard" / "nginx" / "default.conf"


class DashboardNginxTests(unittest.TestCase):
    def test_rcp_web_alias_proxies_only_http_to_wifi_address(self) -> None:
        config = NGINX_CONFIG.read_text(encoding="utf-8")

        self.assertIn("server_name 10.42.1.2;", config)
        self.assertIn("listen 80 default_server;", config)
        self.assertIn("proxy_pass http://host.docker.internal:18080;", config)
        self.assertNotIn("10.42.0.2:6638", config)


if __name__ == "__main__":
    unittest.main()
