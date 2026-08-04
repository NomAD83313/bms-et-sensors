import os
import unittest
from unittest.mock import patch

from app.messkluppe.messkluppe_radio_diag import radio_diag_config_from_env


class MesskluppeRadioDiagTests(unittest.TestCase):
    def test_radio_diag_config_uses_physical_pin_22_default_ce_gpio(self):
        with patch.dict(os.environ, {}, clear=True):
            config = radio_diag_config_from_env()

        self.assertEqual(config["spi_bus"], 0)
        self.assertEqual(config["spi_device"], 0)
        self.assertEqual(config["ce_gpio"], 25)
        self.assertEqual(config["channel"], 111)
        self.assertEqual(config["payload_size"], 32)

    def test_radio_diag_config_accepts_legacy_ce_gpio_override(self):
        with patch.dict(os.environ, {"MESSKLUPPE_RADIO_CE_GPIO": "22"}, clear=True):
            config = radio_diag_config_from_env()

        self.assertEqual(config["ce_gpio"], 22)


if __name__ == "__main__":
    unittest.main()
