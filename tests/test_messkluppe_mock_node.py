import unittest

from app.messkluppe.messkluppe_mock_node import DEFAULT_MOCK_FILE_ID, build_mock_node_sample
from app.messkluppe.messkluppe_protocol import TASK_FILE_DOWNLOAD, decode_file_data_packet


class MesskluppeMockNodeTests(unittest.TestCase):
    def test_mock_node_sample_decodes_as_legacy_file_payload(self):
        sample = build_mock_node_sample(seq=42, unix_time=1_700_000_000)
        packet = decode_file_data_packet(sample.payload)

        self.assertEqual(len(sample.payload), 32)
        self.assertEqual(sample.file_id, DEFAULT_MOCK_FILE_ID)
        self.assertEqual(packet.clip_id, 1)
        self.assertEqual(packet.task, TASK_FILE_DOWNLOAD)
        self.assertEqual(packet.unix_time, 1_700_000_000)
        self.assertEqual(packet.line_number, 42)
        self.assertEqual(packet.values[0], (42 * 137) % 1000)


if __name__ == "__main__":
    unittest.main()
