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

    def target_node_ids(self) -> list[int]:
        node_ids: list[int] = []
        if self._config.primary_node_id > 0:
            node_ids.append(self._config.primary_node_id)
        try:
            for node in self._get_node_snapshot():
                node_id = node.get("node_id")
                if isinstance(node_id, int) and node_id > 0 and node_id not in node_ids:
                    node_ids.append(node_id)
        except Exception as exc:
            self._log(f"matter poll target discovery failed: {exc}")
        return node_ids

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

        target_node_ids = self.target_node_ids()
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

        if primary_node_id > 0:
            for attribute_id in (11, 12, 26):
                self.poll_numeric_attribute(
                    primary_node_id,
                    self._config.battery_endpoint_id,
                    "47",
                    str(attribute_id),
                )

        for node_id in target_node_ids:
            for endpoint_id, cluster_id, attribute_id in self._config.environment_attributes:
                self.poll_numeric_attribute(node_id, endpoint_id, cluster_id, attribute_id)
