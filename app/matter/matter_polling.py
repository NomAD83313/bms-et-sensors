from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_ENVIRONMENT_ATTRIBUTES: tuple[tuple[int, str, str], ...] = (
    (1, "1026", "0"),
    (2, "1029", "0"),
    (3, "1027", "0"),
    (3, "1027", "16"),
    (3, "1068", "0"),
    (3, "1066", "0"),
    (3, "1069", "0"),
)

# Power Source: BatVoltage, BatPercentRemaining, BatChargeState.
BATTERY_ATTRIBUTE_IDS: tuple[str, ...] = ("11", "12", "26")


@dataclass(frozen=True)
class PollingConfig:
    interval_sec: float
    primary_node_id: int
    battery_endpoint_id: int
    environment_attributes: tuple[tuple[int, str, str], ...] = DEFAULT_ENVIRONMENT_ATTRIBUTES


class MatterPollingService:
    def __init__(
        self,
        config: PollingConfig,
        read_attribute: Callable[[int, str], Any],
        get_node_snapshot: Callable[[], list[dict[str, Any]]],
        record_event: Callable[[dict[str, Any]], None],
        log: Callable[[str], None],
    ) -> None:
        self._config = config
        self._read_attribute = read_attribute
        self._get_node_snapshot = get_node_snapshot
        self._record_event = record_event
        self._log = log

    def _load_node_snapshot(self) -> list[dict[str, Any]]:
        try:
            nodes = self._get_node_snapshot()
        except Exception as exc:
            self._log(f"matter poll target discovery failed: {exc}")
            return []
        return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []

    def target_node_ids(self, nodes: list[dict[str, Any]] | None = None) -> list[int]:
        if nodes is None:
            nodes = self._load_node_snapshot()
        node_ids: list[int] = []
        if self._config.primary_node_id > 0:
            node_ids.append(self._config.primary_node_id)
        for node in nodes:
            node_id = node.get("node_id")
            if isinstance(node_id, int) and node_id > 0 and node_id not in node_ids:
                node_ids.append(node_id)
        return node_ids

    def battery_endpoint_ids(self, node_id: int, nodes: list[dict[str, Any]]) -> list[int]:
        """Battery Power Source endpoints for a node.

        Discovered endpoints come from the node snapshot. The configured
        battery endpoint stays as a fallback for the primary node so it is
        still polled when the snapshot is empty or not yet interviewed.
        """
        endpoint_ids: list[int] = []
        if node_id == self._config.primary_node_id and self._config.battery_endpoint_id >= 0:
            endpoint_ids.append(self._config.battery_endpoint_id)
        for node in nodes:
            if node.get("node_id") != node_id:
                continue
            for endpoint_id in node.get("battery_endpoints") or []:
                if isinstance(endpoint_id, int) and endpoint_id >= 0 and endpoint_id not in endpoint_ids:
                    endpoint_ids.append(endpoint_id)
        return endpoint_ids

    def poll_numeric_attribute(
        self,
        node_id: int,
        endpoint_id: int,
        cluster_id: str,
        attribute_id: str = "0",
    ) -> None:
        value = self._read_attribute(node_id, f"{endpoint_id}/{cluster_id}/{attribute_id}")
        if not isinstance(value, (int, float)):
            return
        self._record_event(
            {
                "event_type": "poll_attribute",
                "tags": {
                    "node_id": str(node_id),
                    "endpoint_id": str(endpoint_id),
                    "cluster_id": cluster_id,
                    "attribute_id": attribute_id,
                },
                "fields": {"value": float(value)},
            }
        )

    def poll_once(self) -> None:
        if self._config.interval_sec <= 0:
            return

        nodes = self._load_node_snapshot()
        target_node_ids = self.target_node_ids(nodes)
        primary_node_id = self._config.primary_node_id
        local_temp_centi = self._read_attribute(primary_node_id, "1/513/0")
        heat_setpoint_centi = self._read_attribute(primary_node_id, "1/513/18")
        fields: dict[str, float] = {}
        if isinstance(local_temp_centi, (int, float)):
            fields["thermostat_local_temperature_c"] = round(float(local_temp_centi) / 100.0, 2)
        if isinstance(heat_setpoint_centi, (int, float)):
            fields["thermostat_occupied_heating_setpoint_c"] = round(float(heat_setpoint_centi) / 100.0, 2)
        if fields:
            self._record_event(
                {
                    "event_type": "poll_snapshot",
                    "tags": {"node_id": str(primary_node_id)},
                    "fields": fields,
                }
            )

        for node_id in target_node_ids:
            for battery_endpoint_id in self.battery_endpoint_ids(node_id, nodes):
                for attribute_id in BATTERY_ATTRIBUTE_IDS:
                    self.poll_numeric_attribute(node_id, battery_endpoint_id, "47", attribute_id)
            for endpoint_id, cluster_id, attribute_id in self._config.environment_attributes:
                self.poll_numeric_attribute(node_id, endpoint_id, cluster_id, attribute_id)
