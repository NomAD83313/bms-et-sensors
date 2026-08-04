import unittest

from app.mscl.mscl_tx_power_helpers import (
    allowed_tx_powers,
    normalize_tx_power,
    tx_power_to_enum,
)


def _is_tc_link(model):
    return "tc-link-200-oem" in str(model or "").lower()


class TxPowerHelpersTests(unittest.TestCase):
    def test_allowed_tx_powers(self):
        self.assertEqual(allowed_tx_powers("tc-link-200-oem", _is_tc_link), [10, 5, 0])
        self.assertEqual(allowed_tx_powers("other", _is_tc_link), [16, 10, 5, 0])

    def test_tx_power_to_enum(self):
        self.assertEqual(tx_power_to_enum(16), 1)
        self.assertEqual(tx_power_to_enum(10), 2)
        self.assertEqual(tx_power_to_enum(5), 3)
        self.assertEqual(tx_power_to_enum(0), 4)
        self.assertEqual(tx_power_to_enum(999), 1)

    def test_normalize_table(self):
        cases = [
            ("generic_keep", 16, "generic", 16, 1, None),
            ("generic_fallback_down", 12, "generic", 10, 2, "unsupported"),
            ("generic_fallback_min", -5, "generic", 0, 4, "unsupported"),
            ("tc_keep", 10, "tc-link-200-oem", 10, 2, None),
            ("tc_invalid_to_default", "bad", "tc-link-200-oem", 10, 2, None),
            ("tc_16_to_10", 16, "tc-link-200-oem", 10, 2, "unsupported"),
            ("none_to_default", None, "generic", 16, 1, None),
        ]
        for _name, tx_in, model, tx_out, enum_out, warn_part in cases:
            out = normalize_tx_power(tx_in, model, _is_tc_link)
            self.assertEqual(out["tx_power"], tx_out)
            self.assertEqual(out["tx_enum"], enum_out)
            if warn_part is None:
                self.assertIsNone(out["warning"])
            else:
                self.assertIn(warn_part, out["warning"])


if __name__ == "__main__":
    unittest.main()
