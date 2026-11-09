import unittest
from types import SimpleNamespace

from app.redlab.redlab_inventory import (
    canonical_redlab_device_id,
    descriptor_has_valid_unique_id,
    is_valid_redlab_unique_id,
    normalize_redlab_descriptor,
    normalize_redlab_inventory,
    redlab_descriptor_matches,
    select_redlab_descriptor,
)


class RedLabInventoryTests(unittest.TestCase):
    def test_canonical_device_id_uses_unique_id(self):
        self.assertEqual(canonical_redlab_device_id("01A31CE0"), "redlab_01A31CE0")

    def test_canonical_device_id_handles_missing_unique_id(self):
        self.assertEqual(canonical_redlab_device_id(""), "redlab_unknown")

    def test_invalid_unique_ids_are_not_device_targets(self):
        self.assertFalse(is_valid_redlab_unique_id(""))
        self.assertFalse(is_valid_redlab_unique_id("NO PERMISSION"))
        self.assertFalse(is_valid_redlab_unique_id("unknown"))
        self.assertTrue(is_valid_redlab_unique_id("01A31CE0"))

    def test_descriptor_validity_uses_unique_id(self):
        self.assertFalse(descriptor_has_valid_unique_id(SimpleNamespace(unique_id="NO PERMISSION")))
        self.assertTrue(descriptor_has_valid_unique_id(SimpleNamespace(unique_id="0233CFAA")))

    def test_normalizes_descriptor_to_json_safe_record(self):
        descriptor = SimpleNamespace(
            product_name="USB-TC",
            unique_id="0233CFAA",
            product_id=144,
        )

        record = normalize_redlab_descriptor(
            descriptor,
            index=1,
            active_unique_id="0233CFAA",
        )

        self.assertEqual(record["index"], 1)
        self.assertEqual(record["device_id"], "redlab_0233CFAA")
        self.assertEqual(record["unique_id"], "0233CFAA")
        self.assertEqual(record["product_name"], "USB-TC")
        self.assertEqual(record["product_id"], 144)
        self.assertEqual(record["display_name"], "USB-TC 0233CFAA")
        self.assertTrue(record["connected"])
        self.assertTrue(record["active"])

    def test_active_can_match_canonical_device_id(self):
        descriptor = SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0")

        record = normalize_redlab_descriptor(
            descriptor,
            active_unique_id="redlab_01A31CE0",
        )

        self.assertTrue(record["active"])

    def test_inventory_preserves_descriptor_order(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
            SimpleNamespace(product_name="USB-TC", unique_id="0233CFAA"),
        ]

        records = normalize_redlab_inventory(descriptors, active_unique_id="0233CFAA")

        self.assertEqual([record["unique_id"] for record in records], ["01A31CE0", "0233CFAA"])
        self.assertFalse(records[0]["active"])
        self.assertTrue(records[1]["active"])

    def test_inventory_skips_invalid_unique_ids(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="NO PERMISSION"),
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
        ]

        records = normalize_redlab_inventory(descriptors)

        self.assertEqual([record["unique_id"] for record in records], ["01A31CE0"])
        self.assertEqual(records[0]["index"], 0)

    def test_inventory_can_mark_multiple_active_devices(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
            SimpleNamespace(product_name="USB-TC", unique_id="0233CFAA"),
        ]

        records = normalize_redlab_inventory(
            descriptors,
            active_device_ids={"redlab_01A31CE0", "redlab_0233CFAA"},
        )

        self.assertTrue(records[0]["active"])
        self.assertTrue(records[1]["active"])

    def test_descriptor_match_accepts_unique_id_or_canonical_id(self):
        descriptor = SimpleNamespace(product_name="USB-TC", unique_id="0233CFAA")

        self.assertTrue(redlab_descriptor_matches(descriptor, "0233CFAA"))
        self.assertTrue(redlab_descriptor_matches(descriptor, "redlab_0233CFAA"))
        self.assertFalse(redlab_descriptor_matches(descriptor, "redlab_01A31CE0"))

    def test_descriptor_match_rejects_invalid_unique_id(self):
        descriptor = SimpleNamespace(product_name="USB-TC", unique_id="NO PERMISSION")

        self.assertFalse(redlab_descriptor_matches(descriptor, "NO PERMISSION"))
        self.assertFalse(redlab_descriptor_matches(descriptor, "redlab_NO PERMISSION"))

    def test_select_descriptor_defaults_to_first_when_unconfigured(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
            SimpleNamespace(product_name="USB-TC", unique_id="0233CFAA"),
        ]

        selected = select_redlab_descriptor(descriptors)

        self.assertIs(selected, descriptors[0])

    def test_select_descriptor_skips_invalid_default(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="NO PERMISSION"),
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
        ]

        selected = select_redlab_descriptor(descriptors)

        self.assertIs(selected, descriptors[1])

    def test_select_descriptor_uses_configured_hardware_id(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
            SimpleNamespace(product_name="USB-TC", unique_id="0233CFAA"),
        ]

        selected = select_redlab_descriptor(descriptors, "redlab_0233CFAA")

        self.assertIs(selected, descriptors[1])

    def test_select_descriptor_returns_none_for_missing_configured_device(self):
        descriptors = [
            SimpleNamespace(product_name="USB-TC", unique_id="01A31CE0"),
        ]

        selected = select_redlab_descriptor(descriptors, "0233CFAA")

        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()
