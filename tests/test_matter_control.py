import unittest
from unittest.mock import Mock, patch

from app.matter import matter_control


class MatterControlTests(unittest.TestCase):
    def setUp(self) -> None:
        matter_control.reset_docker_client()

    def tearDown(self) -> None:
        matter_control.reset_docker_client()

    def test_docker_client_is_created_lazily_and_reused(self) -> None:
        docker_client = object()

        with patch.object(matter_control, "create_client", return_value=docker_client) as create_client:
            self.assertIs(matter_control.get_docker_client(), docker_client)
            self.assertIs(matter_control.get_docker_client(), docker_client)

        create_client.assert_called_once_with()

    def test_openthread_restart_stops_and_starts_container(self) -> None:
        docker_client = object()

        with (
            patch.object(matter_control, "get_docker_client", return_value=docker_client),
            patch.object(
                matter_control,
                "set_container_running",
                side_effect=["stop:openthread-border-router", "start:openthread-border-router"],
            ) as set_running,
            patch.object(matter_control, "control_target_payload", return_value={"all_running": True, "services": {}}),
        ):
            result = matter_control.control_target_action("openthread", "restart")

        self.assertTrue(result["success"])
        self.assertEqual(result["actions"], ["stop:openthread-border-router", "start:openthread-border-router"])
        self.assertEqual(
            set_running.call_args_list,
            [
                unittest.mock.call(docker_client, "openthread-border-router", False),
                unittest.mock.call(docker_client, "openthread-border-router", True),
            ],
        )

    def test_matter_server_restart_requires_host_script_without_docker_access(self) -> None:
        with patch.object(matter_control, "get_docker_client", Mock()) as get_client:
            result = matter_control.control_target_action("matter-server", "restart")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "host_restart_required")
        get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
