import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "openthread-host-setup.sh"


class OpenThreadHostSetupTests(unittest.TestCase):
    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _make_fake_proc_ns(self, root: Path, init_mnt: str, self_mnt: str) -> None:
        (root / "1" / "ns").mkdir(parents=True)
        (root / "self" / "ns").mkdir(parents=True)
        os.symlink(init_mnt, root / "1" / "ns" / "mnt")
        os.symlink(self_mnt, root / "self" / "ns" / "mnt")

    def _make_base_env(self, tmp: Path) -> dict[str, str]:
        fake_bin = tmp / "bin"
        fake_dev = tmp / "dev"
        fake_sys_net = tmp / "sys" / "class" / "net"
        fake_sysctl_conf = tmp / "99-bms-openthread.conf"

        fake_bin.mkdir()
        (fake_dev / "net").mkdir(parents=True)
        (fake_dev / "ttyACM0").touch()
        (fake_dev / "net" / "tun").touch()
        (fake_sys_net / "wlan1").mkdir(parents=True)
        fake_sysctl_conf.write_text("net.ipv6.conf.all.forwarding=1\n", encoding="utf-8")

        self._write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(fake_bin / "ip", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(fake_bin / "nc", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(fake_bin / "socat", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(fake_bin / "sudo", "#!/usr/bin/env bash\nexec \"$@\"\n")
        self._write_executable(fake_bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(
            fake_bin / "sysctl",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                if [[ "$1" == "-w" ]]; then
                  exit 0
                fi
                if [[ "$1" == "-n" && "$2" == "net.ipv6.conf.all.disable_ipv6" ]]; then
                  echo 0
                  exit 0
                fi
                if [[ "$1" == "-n" && "$2" == "net.ipv6.conf.all.forwarding" ]]; then
                  echo 1
                  exit 0
                fi
                exit 1
                """
            ),
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "BMS_OTBR_DEV_ROOT": str(fake_dev),
                "BMS_OTBR_SYS_CLASS_NET_ROOT": str(fake_sys_net),
                "BMS_OTBR_SYSCTL_CONF": str(fake_sysctl_conf),
                "ENV_FILE": str(tmp / "missing.env"),
            }
        )
        return env

    def test_warns_when_running_outside_init_mount_namespace(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            fake_proc = tmp / "proc"
            self._make_fake_proc_ns(fake_proc, "mnt:[host]", "mnt:[agent]")
            env = self._make_base_env(tmp)
            env["BMS_OTBR_PROC_ROOT"] = str(fake_proc)

            result = subprocess.run(
                [str(SCRIPT), "--check"],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("agent/container namespace", result.stderr)
            self.assertIn("false negatives", result.stderr)

    def test_no_namespace_warning_when_mount_namespace_matches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            fake_proc = tmp / "proc"
            self._make_fake_proc_ns(fake_proc, "mnt:[host]", "mnt:[host]")
            env = self._make_base_env(tmp)
            env["BMS_OTBR_PROC_ROOT"] = str(fake_proc)

            result = subprocess.run(
                [str(SCRIPT), "--check"],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("agent/container namespace", result.stderr)
            self.assertIn("OpenThread host checks passed", result.stdout)

    def test_network_rcp_bridge_service_waits_for_tcp_before_pty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            env_file = tmp / ".env"
            service_file = tmp / "bms-otbr-rcp-bridge.service"
            env_file.write_text(
                "OTBR_RCP_DEVICE=/dev/ttyOTBR\n"
                "OTBR_RCP_TCP_ENDPOINT=10.42.0.2:6638\n",
                encoding="utf-8",
            )
            env = self._make_base_env(tmp)
            env["ENV_FILE"] = str(env_file)
            env["BMS_OTBR_RCP_BRIDGE_SERVICE"] = str(service_file)

            result = subprocess.run(
                [str(SCRIPT), "--apply"],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            service = service_file.read_text(encoding="utf-8")
            self.assertIn(
                "ExecStart=/usr/bin/socat -d -d TCP:10.42.0.2:6638,forever,interval=5 "
                "PTY,raw,echo=0,link=/dev/ttyOTBR,ignoreeof",
                service,
            )


if __name__ == "__main__":
    unittest.main()
