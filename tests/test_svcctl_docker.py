import types
import unittest
from unittest.mock import Mock

from tests.isolated_import import load_isolated_module


class FakeNotFound(Exception):
    pass


def _load_module():
    fake_docker = types.ModuleType("docker")
    fake_docker.DockerClient = lambda *_args, **_kwargs: object()
    fake_docker.errors = types.SimpleNamespace(NotFound=FakeNotFound)
    return load_isolated_module("app.svcctl.svcctl_docker", {"docker": fake_docker})


class MissingContainerActionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_start_missing_container_reports_compose_required(self):
        class Containers:
            def get(self, _name):
                raise FakeNotFound("missing")

        client = types.SimpleNamespace(containers=Containers())
        self.assertEqual(
            self.mod.set_container_running(client, "pyrometer-collector", True),
            "missing:pyrometer-collector:compose_up_required",
        )

    def test_stop_missing_container_is_noop(self):
        class Containers:
            def get(self, _name):
                raise FakeNotFound("missing")

        client = types.SimpleNamespace(containers=Containers())
        self.assertEqual(
            self.mod.set_container_running(client, "pyrometer-collector", False),
            "noop:pyrometer-collector:missing",
        )


class ComposeCameraTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.controller = types.SimpleNamespace(labels={"com.docker.compose.project": "custom-stack"})
        self.camera = Mock(status="running")
        self.client = types.SimpleNamespace(containers=Mock())
        self.client.containers.get.return_value = self.controller
        self.client.containers.list.return_value = [self.camera]

    def test_camera_lookup_uses_project_and_service_labels(self):
        self.assertEqual(self.mod.container_status(self.client, "camera-media"), "running")
        self.client.containers.get.assert_called_once_with("service-controller")
        self.client.containers.list.assert_called_once_with(all=True, filters={"label": [
            "com.docker.compose.project=custom-stack",
            "com.docker.compose.service=camera-media",
            "com.docker.compose.oneoff=False",
        ]})
        self.mod.set_container_running(self.client, "camera-media", False)
        self.camera.stop.assert_called_once_with(timeout=15)

    def test_absent_camera_profile_never_creates_containers(self):
        self.client.containers.list.return_value = []
        self.assertEqual(self.mod.container_status(self.client, "camera-analysis"), "missing")
        self.assertEqual(self.mod.set_container_running(self.client, "camera-analysis", True),
                         "missing:camera-analysis:compose_up_required")
        self.assertEqual(self.mod.set_container_running(self.client, "camera-analysis", False),
                         "noop:camera-analysis:missing")
        self.camera.start.assert_not_called()
        self.camera.stop.assert_not_called()

    def test_ambiguous_or_unscoped_camera_cannot_be_stopped(self):
        self.client.containers.list.return_value = [self.camera, Mock()]
        with self.assertRaises(RuntimeError):
            self.mod.set_container_running(self.client, "camera-media", False)
        self.controller.labels = {}
        with self.assertRaises(RuntimeError):
            self.mod.set_container_running(self.client, "camera-media", False)
        self.camera.stop.assert_not_called()

    def test_existing_named_service_lookup_is_unchanged(self):
        self.client.containers.get.return_value = self.camera
        self.assertEqual(self.mod.container_status(self.client, "mscl-collector"), "running")
        self.client.containers.get.assert_called_once_with("mscl-collector")
        self.client.containers.list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
