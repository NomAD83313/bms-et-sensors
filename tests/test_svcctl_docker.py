import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
