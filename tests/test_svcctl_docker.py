import importlib
import sys
import types
import unittest


class FakeNotFound(Exception):
    pass


def _load_module():
    sys.modules.pop("app.svcctl.svcctl_docker", None)
    sys.modules.pop("svcctl_docker", None)

    fake_docker = types.ModuleType("docker")
    fake_docker.DockerClient = lambda *_args, **_kwargs: object()
    fake_docker.errors = types.SimpleNamespace(NotFound=FakeNotFound)
    sys.modules["docker"] = fake_docker

    return importlib.import_module("app.svcctl.svcctl_docker")


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
