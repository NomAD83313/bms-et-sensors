import tempfile
import unittest
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
GRAF_APP_DIR = ROOT_DIR / "app" / "graf"
if str(GRAF_APP_DIR) not in sys.path:
    sys.path.insert(0, str(GRAF_APP_DIR))

from app.graf.graf_csv_helpers import matter_csv_column_name, messkluppe_csv_column_name, pyrometers_csv_column_name, redlab_csv_column_name
from app.graf.graf_backend_services import _annotate_pyrometer_serials
from app.graf.graf_query_builders import (
    matter_battery_flux,
    matter_flux,
    messkluppe_flux,
    mscl_flux,
    pyrometers_flux,
    redlab_flux,
    redlab_flux_raw,
)
from app.graf.graf_redlab_state import load_redlab_channels, save_redlab_channels


class GrafRedLabTagTests(unittest.TestCase):
    def test_mscl_flux_can_aggregate_for_historical_view(self):
        query = mscl_flux(
            bucket="sensors",
            measurement="mscl_sensors",
            channel="ch1",
            source_values=["mscl_config_stream", "mscl_node_export"],
            start_expr="-5m",
            stop_expr=None,
            window="2m",
        )

        self.assertIn("aggregateWindow(every: 2m, fn: max, createEmpty: false)", query)
        self.assertIn('"_measurement", "device", "source", "channel", "node_id"', query)

    def test_mscl_flux_keeps_raw_mode_unaggregated(self):
        query = mscl_flux(
            bucket="sensors",
            measurement="mscl_sensors",
            channel="ch1",
            source_values=["mscl_config_stream"],
            start_expr="-5m",
            stop_expr=None,
            window="__raw__",
        )

        self.assertNotIn("aggregateWindow", query)

    def test_redlab_flux_keeps_device_and_channel_tags(self):
        query = redlab_flux(
            bucket="sensors",
            measurement="redlab",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
        )

        self.assertIn('"device", "channel"', query)
        self.assertNotIn("redlab_daq", query)

    def test_redlab_raw_flux_keeps_device_and_channel_tags(self):
        query = redlab_flux_raw(
            bucket="sensors",
            measurement="redlab",
            start_expr="-5m",
            stop_expr=None,
        )

        self.assertIn('"device", "channel"', query)
        self.assertNotIn("redlab_daq", query)

    def test_redlab_csv_column_name_uses_device_and_channel(self):
        name = "device=redlab_01A31CE0 | channel=ch0"
        self.assertEqual(redlab_csv_column_name(name), "ch0")
        self.assertEqual(redlab_csv_column_name(name, device_numbers={}), "d1ch0")

    def test_redlab_csv_column_name_uses_custom_channel_names(self):
        name = "device=redlab_01A31CE0 | channel=ch0"
        self.assertEqual(
            redlab_csv_column_name(name, {"redlab_01A31CE0|ch0": "Left Sensor"}),
            "left_sensor_(ch0)",
        )
        self.assertEqual(
            redlab_csv_column_name(name, {"ch0": "Left Sensor"}),
            "left_sensor_(ch0)",
        )
        self.assertEqual(
            redlab_csv_column_name(
                name,
                {"redlab_01A31CE0|ch0": {"enabled": True, "name": "Left Sensor"}},
            ),
            "left_sensor_(ch0)",
        )
        self.assertEqual(
            redlab_csv_column_name(name, {"redlab_01A31CE0|ch0": True}),
            "ch0",
        )

    def test_redlab_csv_column_name_assigns_device_numbers_in_first_seen_order(self):
        device_numbers = {}
        self.assertEqual(
            redlab_csv_column_name("device=redlab_A | channel=ch0", device_numbers=device_numbers),
            "d1ch0",
        )
        self.assertEqual(
            redlab_csv_column_name("device=redlab_A | channel=ch1", device_numbers=device_numbers),
            "d1ch1",
        )
        self.assertEqual(
            redlab_csv_column_name("device=redlab_B | channel=ch0", device_numbers=device_numbers),
            "d2ch0",
        )

    def test_pyrometers_flux_selects_only_object_temperature(self):
        query = pyrometers_flux(
            bucket="sensors",
            measurement="pyrometers",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
        )

        self.assertIn('r._field == "object_temperature_c"', query)
        self.assertNotIn('r._field == "sensor_head_temperature_c"', query)
        self.assertNotIn('r._field == "controller_box_temperature_c"', query)
        self.assertIn('"_time", "_value", "_field", "source", "device", "serial"', query)

    def test_pyrometers_csv_column_name_uses_temperature_channel_aliases(self):
        self.assertEqual(
            pyrometers_csv_column_name("source=optris2 | device=OPTRIS_CT | _field=object_temperature_c"),
            "optris2_tobj",
        )
        self.assertEqual(
            pyrometers_csv_column_name("source=optris2 | device=OPTRIS_CT | serial=CT00028511 | _field=object_temperature_c"),
            "CT00028511_tobj",
        )
        self.assertEqual(
            pyrometers_csv_column_name("source=optris2 | device=OPTRIS_CT | _field=sensor_head_temperature_c"),
            "optris2_thead",
        )
        self.assertEqual(
            pyrometers_csv_column_name("source=optris2 | device=OPTRIS_CT | _field=controller_box_temperature_c"),
            "optris2_tbox",
        )
    def test_pyrometers_series_names_can_be_annotated_from_registry_serials(self):
        series = [
            {
                "name": "source=optris2 | device=OPTRIS_CT | _field=object_temperature_c",
                "points": [{"t": "2026-05-11T10:00:00Z", "v": 25.0}],
            }
        ]

        annotated = _annotate_pyrometer_serials(series, {"optris2": "CT00028511"})

        self.assertEqual(
            annotated[0]["name"],
            "source=optris2 | device=OPTRIS_CT | serial=CT00028511 | _field=object_temperature_c",
        )

    def test_messkluppe_flux_selects_force_fields(self):
        query = messkluppe_flux(
            bucket="sensors",
            measurement="messkluppe_sensor",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
        )

        self.assertIn('r._measurement == "messkluppe_sensor"', query)
        self.assertIn('r._field == "force_x_raw"', query)
        self.assertIn('r._field == "force_y_raw"', query)
        self.assertIn('r._field == "force_z_raw"', query)
        self.assertIn('"clip_id", "file_id"', query)

    def test_messkluppe_flux_can_select_orientation_fields(self):
        query = messkluppe_flux(
            bucket="sensors",
            measurement="messkluppe_sensor",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
            fields=("yaw_deg",),
        )

        self.assertIn('r._field == "yaw_deg"', query)
        self.assertNotIn('r._field == "force_x_raw"', query)
        self.assertNotIn('r._field == "accel_x_raw"', query)

    def test_messkluppe_csv_column_name_uses_clip_file_and_field(self):
        name = "source=messkluppe | clip_id=1 | file_id=fake | _field=force_x_raw"
        self.assertEqual(messkluppe_csv_column_name(name), "messkluppe_clip_1_file_fake_force_x_raw")

    def test_matter_battery_flux_selects_power_source_percent(self):
        query = matter_battery_flux(
            bucket="sensors",
            measurement="matter_sensor",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
        )

        self.assertIn('r.event_type == "attribute_updated" or r.event_type == "poll_attribute"', query)
        self.assertIn('r.cluster_id == "47"', query)
        self.assertIn('r.attribute_id == "12"', query)
        self.assertIn("_value: r._value / 2.0", query)

    def test_matter_flux_can_select_humidity_and_pressure_clusters(self):
        humidity_query = matter_flux(
            bucket="sensors",
            measurement="matter_sensor",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
            cluster_id="1029",
        )
        pressure_query = matter_flux(
            bucket="sensors",
            measurement="matter_sensor",
            start_expr="-5m",
            stop_expr=None,
            window="1s",
            cluster_id="1027",
            attribute_id="16",
        )

        self.assertIn('r.cluster_id == "1029"', humidity_query)
        self.assertIn('r.cluster_id == "1027"', pressure_query)
        self.assertIn('r.attribute_id == "16"', pressure_query)
        self.assertIn('r.event_type == "attribute_updated" or r.event_type == "poll_attribute"', humidity_query)
        self.assertIn("_value: r._value / 100.0", humidity_query)
        self.assertIn("_value: r._value / 10.0", pressure_query)

    def test_matter_csv_column_name_includes_environment_cluster(self):
        self.assertEqual(
            matter_csv_column_name("source=matter-server | node_id=15 | endpoint_id=2 | cluster_id=1029"),
            "matter_node_15_ep_2_humidity",
        )
        self.assertEqual(
            matter_csv_column_name("source=matter-server | node_id=15 | endpoint_id=3 | cluster_id=1027"),
            "matter_node_15_ep_3_pressure_hpa",
        )

    def test_redlab_channel_state_preserves_device_channel_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "redlab_channels.json"
            saved = save_redlab_channels(
                path,
                ["ch0", "ch1"],
                {
                    "ch0": False,
                    "redlab_01A31CE0|ch0": True,
                    "redlab_0233CFAA|ch1": False,
                    "bad_device|ch9": False,
                },
            )

            self.assertEqual(saved["ch0"], False)
            self.assertEqual(saved["redlab_01A31CE0|ch0"], True)
            self.assertEqual(saved["redlab_0233CFAA|ch1"], False)
            self.assertNotIn("bad_device|ch9", saved)
            self.assertEqual(load_redlab_channels(path, ["ch0", "ch1"]), saved)


if __name__ == "__main__":
    unittest.main()
