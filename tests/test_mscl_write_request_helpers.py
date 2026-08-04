import unittest

from app.mscl.mscl_write_request_helpers import WriteRequestValidationError, validate_write_request


class WriteRequestHelpersTests(unittest.TestCase):
    def test_invalid_json(self):
        with self.assertRaises(WriteRequestValidationError) as cm:
            validate_write_request(None, {})
        self.assertIn("Invalid JSON body", str(cm.exception))

    def test_invalid_node_id(self):
        with self.assertRaises(WriteRequestValidationError) as cm:
            validate_write_request({"node_id": "bad"}, {})
        self.assertIn("node_id is required", str(cm.exception))

    def test_unknown_sample_rate(self):
        with self.assertRaises(WriteRequestValidationError) as cm:
            validate_write_request({"node_id": 16904, "sample_rate": ""}, {})
        self.assertIn("Sample Rate is unknown", str(cm.exception))

    def test_success(self):
        node_id, parsed = validate_write_request({"node_id": "16904", "sample_rate": "112"}, {})
        self.assertEqual(node_id, 16904)
        self.assertEqual(parsed["sample_rate"], 112)


if __name__ == "__main__":
    unittest.main()
