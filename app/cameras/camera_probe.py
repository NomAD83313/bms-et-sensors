import ipaddress
import json
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote


def build_rtsp_url(camera: dict, redact: bool = False) -> str:
    username = str(camera.get("username", "thingino"))
    password = "***" if redact else str(camera.get("password", "thingino"))
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
    address = camera["address"]
    port = int(camera.get("port", 554))
    path = str(camera.get("path", "ch0")).lstrip("/")
    return f"rtsp://{credentials}{address}:{port}/{path}"


def probe_camera(camera: dict, timeout: int = 8) -> dict:
    command = [
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
        "-show_entries", "stream=index,codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", build_rtsp_url(camera),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"online": False, "error": "RTSP probe timed out"}
    if result.returncode != 0:
        error = result.stderr.strip().splitlines()
        return {"online": False, "error": error[-1] if error else "RTSP probe failed"}
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        streams = []
    return {"online": bool(streams), "streams": streams}


def discover_rtsp_hosts(subnet: str, port: int = 554, timeout: float = 0.18) -> list[str]:
    network = ipaddress.ip_network(subnet, strict=False)

    def is_open(address: ipaddress.IPv4Address) -> str | None:
        try:
            with socket.create_connection((str(address), port), timeout=timeout):
                return str(address)
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=48) as executor:
        results = executor.map(is_open, network.hosts())
    return [address for address in results if address]
