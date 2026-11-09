from __future__ import annotations

from typing import Any


def _normalize_ext(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = "".join(ch for ch in text if ch in "0123456789abcdef")
    if not text or set(text) == {"0"}:
        return None
    return text.zfill(16)[-16:]


def _normalize_rloc(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = "".join(ch for ch in text if ch in "0123456789abcdef")
    return f"0x{text.zfill(4)[-4:]}" if text else None


def _int_value(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        text = str(value).strip()
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except (TypeError, ValueError):
        return None


def _table_rows(parsed_table: Any) -> list[dict[str, str]]:
    if not isinstance(parsed_table, dict):
        return []
    headers = parsed_table.get("headers")
    rows = parsed_table.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        return []
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        row_map: dict[str, str] = {}
        for index, header in enumerate(headers):
            if index < len(row):
                row_map[str(header)] = str(row[index])
        result.append(row_map)
    return result


def _find_value(row: dict[str, Any], *names: str) -> Any:
    normalized = {"".join(ch for ch in key.lower() if ch.isalnum()): value for key, value in row.items()}
    for name in names:
        token = "".join(ch for ch in name.lower() if ch.isalnum())
        if token in normalized:
            return normalized[token]
    return None


def _node_key(prefix: str, ext_address: str | None, rloc16: str | None) -> str:
    if ext_address:
        return f"{prefix}:ext:{ext_address}"
    if rloc16:
        return f"{prefix}:rloc:{rloc16}"
    return f"{prefix}:unknown"


def _merge_observed_node(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged["source"] = sorted(set((existing.get("source") or []) + (incoming.get("source") or [])))
    for key in ("node_class", "role", "ext_address", "rloc16", "link_quality_in", "link_quality_out", "average_rssi_dbm", "last_rssi_dbm", "timeout_sec", "age_sec"):
        if merged.get(key) in (None, "") and incoming.get(key) not in (None, ""):
            merged[key] = incoming.get(key)
    if str(merged.get("role") or "").lower() not in {"leader", "router", "child"} and incoming.get("role"):
        merged["role"] = incoming.get("role")
    return merged


def _matter_nodes(matter_nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ext_owners: dict[str, set[Any]] = {}
    for node in matter_nodes:
        if not isinstance(node, dict) or not node.get("available"):
            continue
        if str(node.get("network_type") or "") != "Thread":
            continue
        ext_address = _normalize_ext(node.get("ext_address"))
        if ext_address:
            ext_owners.setdefault(ext_address, set()).add(node.get("node_id"))

    ambiguous_ext = {ext for ext, owners in ext_owners.items() if len(owners) > 1}
    normalized_nodes: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for node in matter_nodes:
        if not isinstance(node, dict):
            continue
        ext_address = _normalize_ext(node.get("ext_address"))
        rloc16 = _normalize_rloc(node.get("rloc16"))
        available = bool(node.get("available"))
        is_thread = str(node.get("network_type") or "") == "Thread"
        trusted = bool(ext_address) and not (available and ext_address in ambiguous_ext)
        diagnostics = node.get("thread_diagnostics") if isinstance(node.get("thread_diagnostics"), dict) else {}
        observed_neighbor_exts: set[str] = set()
        observed_neighbor_rlocs: set[str] = set()
        observed_neighbor_links: list[dict[str, Any]] = []
        for table_name in ("neighbor_table", "route_table"):
            for item in diagnostics.get(table_name) or []:
                if not isinstance(item, dict):
                    continue
                item_ext = _normalize_ext(item.get("ext_address"))
                item_rloc = _normalize_rloc(item.get("rloc16"))
                if item_ext:
                    observed_neighbor_exts.add(item_ext)
                if item_rloc:
                    observed_neighbor_rlocs.add(item_rloc)
                if table_name == "neighbor_table" and (item_ext or item_rloc):
                    observed_neighbor_links.append(
                        {
                            "ext_address": item_ext,
                            "rloc16": item_rloc,
                            "link_quality_in": _int_value(item.get("link_quality_in")),
                            "link_quality_out": _int_value(item.get("link_quality_out")),
                            "average_rssi_dbm": _int_value(item.get("average_rssi_dbm")),
                            "last_rssi_dbm": _int_value(item.get("last_rssi_dbm")),
                            "age_sec": _int_value(item.get("age_sec")),
                            "is_child": item.get("is_child"),
                        }
                    )
        record = {
            "node_class": "matter_node",
            "source": ["matter"],
            "matter_node_id": node.get("node_id"),
            "serial_number": node.get("serial_number"),
            "product_name": node.get("product_name"),
            "vendor_name": node.get("vendor_name"),
            "available": available,
            "network_type": node.get("network_type"),
            "standard_controls": node.get("standard_controls") if isinstance(node.get("standard_controls"), list) else [],
            "air_reboot_supported": bool(node.get("air_reboot_supported")),
            "reboot_count": node.get("reboot_count"),
            "uptime_sec": node.get("uptime_sec"),
            "total_operational_hours": node.get("total_operational_hours"),
            "boot_reason": node.get("boot_reason"),
            "boot_reason_label": node.get("boot_reason_label"),
            "diagnostics_observed_at": node.get("diagnostics_observed_at"),
            "estimated_last_boot_at": node.get("estimated_last_boot_at"),
            "role": str(node.get("thread_role") or "").strip().lower() or None,
            "channel": node.get("channel"),
            "ext_address": ext_address if is_thread and trusted else None,
            "rloc16": rloc16 if is_thread and trusted else None,
            "reported_ext_address": ext_address if is_thread else None,
            "reported_rloc16": rloc16 if is_thread else None,
            "address_trusted": trusted if is_thread else None,
            "observed_neighbor_ext_addresses": sorted(observed_neighbor_exts),
            "observed_neighbor_rloc16s": sorted(observed_neighbor_rlocs),
            "observed_neighbor_links": sorted(
                observed_neighbor_links,
                key=lambda item: str(item.get("ext_address") or item.get("rloc16") or ""),
            ),
        }
        if available and ext_address in ambiguous_ext:
            record["address_conflicts"] = ["ext_address"]
            warnings.append(
                {
                    "type": "matter_thread_address_conflict",
                    "matter_node_id": node.get("node_id"),
                    "serial_number": node.get("serial_number"),
                    "ext_address": ext_address,
                    "rloc16": rloc16,
                    "rule": "duplicate_available_matter_ext_address",
                }
            )
        normalized_nodes.append(record)
    return normalized_nodes, warnings


def _parse_otbr(otbr_diag_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(otbr_diag_snapshot, dict) or not otbr_diag_snapshot.get("available"):
        return {"otbr_node": None, "nodes": [], "edges": []}

    settings = otbr_diag_snapshot.get("settings") or {}
    tables = otbr_diag_snapshot.get("tables") or {}
    meshdiag = otbr_diag_snapshot.get("meshdiag") or {}
    otbr_node = {
        "node_class": "otbr",
        "source": ["otbr"],
        "role": str(settings.get("state") or "").strip().lower() or None,
        "channel": _int_value(settings.get("channel")),
        "pan_id": str(settings.get("panid") or "").strip() or None,
        "ext_address": _normalize_ext(settings.get("extaddr")),
        "rloc16": _normalize_rloc(settings.get("rloc16")),
    }
    nodes_by_key: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def upsert(node: dict[str, Any]) -> dict[str, Any]:
        key = _node_key("otbr", node.get("ext_address"), node.get("rloc16"))
        if key in nodes_by_key:
            nodes_by_key[key] = _merge_observed_node(nodes_by_key[key], node)
        else:
            nodes_by_key[key] = node
        return nodes_by_key[key]

    for row in _table_rows((tables.get("router_table") or {}).get("parsed")):
        router = {
            "node_class": "otbr_router",
            "source": ["otbr"],
            "role": "router",
            "ext_address": _normalize_ext(_find_value(row, "extaddr", "extaddress", "extendedmac")),
            "rloc16": _normalize_rloc(_find_value(row, "rloc16")),
            "link_quality_in": _int_value(_find_value(row, "lqin", "linkqualityin")),
            "link_quality_out": _int_value(_find_value(row, "lqout", "linkqualityout")),
            "average_rssi_dbm": _int_value(_find_value(row, "avgrssi", "averagerssi")),
            "last_rssi_dbm": _int_value(_find_value(row, "lastrssi")),
        }
        if not router["ext_address"] and not router["rloc16"]:
            continue
        if router["rloc16"] == otbr_node["rloc16"] and (
            not router["ext_address"] or router["ext_address"] == otbr_node["ext_address"]
        ):
            continue
        upsert(router)
        edges.append(
            {
                "relation": "neighbor",
                "source": ["otbr"],
                "src_ext_address": otbr_node["ext_address"],
                "src_rloc16": otbr_node["rloc16"],
                "dst_ext_address": router["ext_address"],
                "dst_rloc16": router["rloc16"],
                "confidence": "otbr_exact_observation",
            }
        )

    for row in _table_rows((tables.get("neighbor_table") or {}).get("parsed")):
        role_text = str(_find_value(row, "role") or "").strip().lower()
        role = "child" if role_text == "c" else ("router" if role_text == "r" else None)
        neighbor = {
            "node_class": "otbr_neighbor",
            "source": ["otbr"],
            "role": role,
            "ext_address": _normalize_ext(_find_value(row, "extaddr", "extaddress", "extendedmac")),
            "rloc16": _normalize_rloc(_find_value(row, "rloc16")),
            "average_rssi_dbm": _int_value(_find_value(row, "avgrssi", "averagerssi")),
            "last_rssi_dbm": _int_value(_find_value(row, "lastrssi")),
            "age_sec": _int_value(_find_value(row, "age")),
        }
        if not neighbor["ext_address"] and not neighbor["rloc16"]:
            continue
        observed = upsert(neighbor)
        edges.append(
            {
                "relation": "parent_child" if role == "child" else "neighbor",
                "source": ["otbr"],
                "src_ext_address": otbr_node["ext_address"],
                "src_rloc16": otbr_node["rloc16"],
                "dst_ext_address": observed.get("ext_address"),
                "dst_rloc16": observed.get("rloc16"),
                "average_rssi_dbm": neighbor["average_rssi_dbm"],
                "last_rssi_dbm": neighbor["last_rssi_dbm"],
                "confidence": "otbr_neighbor_table",
            }
        )

    meshdiag_routers = ((meshdiag.get("topology_children") or {}).get("parsed") or {}).get("routers") or []
    routers_by_id: dict[int, dict[str, Any]] = {}
    for row in meshdiag_routers:
        if not isinstance(row, dict):
            continue
        flags = [str(flag).strip().lower() for flag in row.get("flags") or []]
        router = {
            "node_class": "otbr_router",
            "source": ["otbr"],
            "role": "leader" if "leader" in flags else "router",
            "router_id": _int_value(row.get("id")),
            "ext_address": _normalize_ext(row.get("ext_address")),
            "rloc16": _normalize_rloc(row.get("rloc16")),
        }
        is_otbr_self = router["rloc16"] == otbr_node["rloc16"] and (
            not router["ext_address"] or router["ext_address"] == otbr_node["ext_address"]
        )
        if (router["ext_address"] or router["rloc16"]) and not is_otbr_self:
            upsert(router)
        if router["router_id"] is not None:
            routers_by_id[router["router_id"]] = router

    for row in _table_rows((tables.get("child_table") or {}).get("parsed")):
        child = {
            "node_class": "otbr_child",
            "source": ["otbr"],
            "role": "child",
            "ext_address": _normalize_ext(_find_value(row, "extaddr", "extaddress", "extendedmac")),
            "rloc16": _normalize_rloc(_find_value(row, "rloc16")),
            "link_quality_in": _int_value(_find_value(row, "lqin", "linkqualityin")),
            "timeout_sec": _int_value(_find_value(row, "timeout")),
            "age_sec": _int_value(_find_value(row, "age")),
        }
        if child["ext_address"] or child["rloc16"]:
            upsert(child)
            edges.append(
                {
                    "relation": "parent_child",
                    "source": ["otbr"],
                    "src_ext_address": otbr_node["ext_address"],
                    "src_rloc16": otbr_node["rloc16"],
                    "dst_ext_address": child["ext_address"],
                    "dst_rloc16": child["rloc16"],
                    "link_quality_in": child["link_quality_in"],
                    "confidence": "otbr_child_table",
                }
            )

    for row in meshdiag_routers:
        if not isinstance(row, dict):
            continue
        router = routers_by_id.get(_int_value(row.get("id")) or -1)
        if not router:
            continue
        for linked_id in row.get("links") or []:
            linked = routers_by_id.get(_int_value(linked_id) or -1)
            if not linked:
                continue
            edges.append(
                {
                    "relation": "neighbor",
                    "source": ["otbr"],
                    "src_ext_address": router.get("ext_address"),
                    "src_rloc16": router.get("rloc16"),
                    "dst_ext_address": linked.get("ext_address"),
                    "dst_rloc16": linked.get("rloc16"),
                    "confidence": "otbr_meshdiag",
                }
            )
        for child_row in row.get("children") or []:
            if not isinstance(child_row, dict):
                continue
            child_rloc = _normalize_rloc(child_row.get("rloc16"))
            existing_child = next(
                (node for node in nodes_by_key.values() if node.get("role") == "child" and node.get("rloc16") == child_rloc),
                None,
            )
            if not existing_child and child_rloc:
                existing_child = upsert(
                    {
                        "node_class": "otbr_child",
                        "source": ["otbr"],
                        "role": "child",
                        "ext_address": None,
                        "rloc16": child_rloc,
                        "link_quality_in": _int_value(child_row.get("link_quality_in")),
                    }
                )
            if child_rloc:
                edges.append(
                    {
                        "relation": "parent_child",
                        "source": ["otbr"],
                        "src_ext_address": router.get("ext_address"),
                        "src_rloc16": router.get("rloc16"),
                        "dst_ext_address": existing_child.get("ext_address") if existing_child else None,
                        "dst_rloc16": child_rloc,
                        "link_quality_in": _int_value(child_row.get("link_quality_in")),
                        "confidence": "otbr_meshdiag",
                    }
                )

    return {"otbr_node": otbr_node, "nodes": list(nodes_by_key.values()), "edges": edges}


def _summary(node: dict[str, Any] | None, fallback: str = "unknown") -> dict[str, Any]:
    if not isinstance(node, dict):
        return {"label": fallback}
    label = node.get("serial_number") or node.get("candidate_label") or node.get("label") or node.get("ext_address") or node.get("rloc16") or fallback
    if node.get("node_class") == "otbr":
        label = "OTBR / RCP"
    return {
        "label": label,
        "node_class": node.get("node_class"),
        "source": list(node.get("source") or []),
        "matter_node_id": node.get("matter_node_id"),
        "serial_number": node.get("serial_number"),
        "product_name": node.get("product_name"),
        "available": node.get("available"),
        "network_type": node.get("network_type"),
        "standard_controls": node.get("standard_controls") if isinstance(node.get("standard_controls"), list) else [],
        "air_reboot_supported": bool(node.get("air_reboot_supported")),
        "reboot_count": node.get("reboot_count"),
        "uptime_sec": node.get("uptime_sec"),
        "total_operational_hours": node.get("total_operational_hours"),
        "boot_reason": node.get("boot_reason"),
        "boot_reason_label": node.get("boot_reason_label"),
        "diagnostics_observed_at": node.get("diagnostics_observed_at"),
        "estimated_last_boot_at": node.get("estimated_last_boot_at"),
        "role": node.get("role"),
        "ext_address": node.get("ext_address"),
        "rloc16": node.get("rloc16"),
        "matched": bool(node.get("matched")),
        "match_rule": node.get("match_rule"),
        "matched_by": node.get("matched_by"),
        "inferred_match": bool(node.get("inferred_match")),
        "candidate_match": bool(node.get("candidate_match")),
        "candidate_label": node.get("candidate_label"),
        "candidate_matter_node_id": node.get("candidate_matter_node_id"),
        "candidate_serial_number": node.get("candidate_serial_number"),
        "candidate_rule": node.get("candidate_rule"),
    }


def _endpoint_matches_node(edge: dict[str, Any], prefix: str, node: dict[str, Any] | None) -> bool:
    if not isinstance(node, dict):
        return False
    edge_ext = _normalize_ext(edge.get(f"{prefix}_ext_address"))
    edge_rloc = _normalize_rloc(edge.get(f"{prefix}_rloc16"))
    node_ext = _normalize_ext(node.get("ext_address"))
    node_rloc = _normalize_rloc(node.get("rloc16"))
    if edge_ext and node_ext:
        return edge_ext == node_ext
    if edge_rloc and node_rloc:
        return edge_rloc == node_rloc
    return False


def _upstream_otbr_link(parent: dict[str, Any] | None, otbr_node: dict[str, Any] | None, edges: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(parent, dict) or not isinstance(otbr_node, dict):
        return None
    if parent.get("node_class") == "otbr":
        return None
    candidates: list[dict[str, Any]] = []
    for edge in edges:
        if edge.get("relation") != "neighbor":
            continue
        src_is_otbr = _endpoint_matches_node(edge, "src", otbr_node)
        dst_is_otbr = _endpoint_matches_node(edge, "dst", otbr_node)
        src_is_parent = _endpoint_matches_node(edge, "src", parent)
        dst_is_parent = _endpoint_matches_node(edge, "dst", parent)
        if not ((src_is_otbr and dst_is_parent) or (dst_is_otbr and src_is_parent)):
            continue
        candidates.append(edge)
    if not candidates:
        return None
    candidates.sort(
        key=lambda edge: (
            0 if edge.get("last_rssi_dbm") is not None or edge.get("average_rssi_dbm") is not None else 1,
            0 if edge.get("link_quality_in") is not None or edge.get("link_quality_out") is not None else 1,
            0 if edge.get("confidence") == "otbr_neighbor_table" else 1,
        )
    )
    edge = candidates[0]
    return {
        "node": _summary(otbr_node, "OTBR / RCP"),
        "relation": "border_router_neighbor",
        "source": list(edge.get("source") or []),
        "link_quality_in": edge.get("link_quality_in"),
        "link_quality_out": edge.get("link_quality_out"),
        "average_rssi_dbm": edge.get("average_rssi_dbm"),
        "last_rssi_dbm": edge.get("last_rssi_dbm"),
        "confidence": "high",
        "match_rule": "otbr_neighbor_edge_only",
    }

def _is_child_role(node: dict[str, Any]) -> bool:
    role = str(node.get("role") or "").strip().lower()
    return not role or role in {"child", "sleepy-child"}


def _observed_parent_keys(node: dict[str, Any]) -> set[str]:
    keys = {
        f"ext:{_normalize_ext(value)}"
        for value in (node.get("observed_neighbor_ext_addresses") or [])
        if _normalize_ext(value)
    }
    keys.update(
        {
            f"rloc:{_normalize_rloc(value)}"
            for value in (node.get("observed_neighbor_rloc16s") or [])
            if _normalize_rloc(value)
        }
    )
    return keys


def _candidate_label(node: dict[str, Any]) -> str | None:
    return node.get("serial_number") or node.get("ext_address")


def _child_inference_record(
    candidate: dict[str, Any],
    *,
    child_ext: str | None,
    child_rloc: str | None,
    matched_by: str,
) -> dict[str, Any]:
    resolved_ext = child_ext
    # Preserve the trusted Matter identity when OTBR only reports a child RLOC.
    if not resolved_ext and matched_by == "same_rloc_and_parent_neighbor_evidence":
        resolved_ext = _normalize_ext(candidate.get("ext_address"))
    return {
        **candidate,
        "source": ["matter", "otbr"],
        "node_class": "matched_node",
        "role": "child",
        "ext_address": resolved_ext,
        "rloc16": child_rloc,
        "matched": True,
        "matched_by": matched_by,
        "match_rule": matched_by,
        "inferred_match": True,
        "candidate_match": True,
        "candidate_label": _candidate_label(candidate),
        "candidate_matter_node_id": candidate.get("matter_node_id"),
        "candidate_serial_number": candidate.get("serial_number"),
        "candidate_rule": matched_by,
        "matter_ext_address": candidate.get("ext_address"),
        "matter_rloc16": candidate.get("rloc16"),
        "otbr_ext_address": child_ext,
        "otbr_rloc16": child_rloc,
    }


def _parent_keys_from_node(node: dict[str, Any] | None) -> set[str]:
    keys: set[str] = set()
    if not isinstance(node, dict):
        return keys
    ext_address = _normalize_ext(node.get("ext_address"))
    rloc16 = _normalize_rloc(node.get("rloc16"))
    if ext_address:
        keys.add(f"ext:{ext_address}")
    if rloc16:
        keys.add(f"rloc:{rloc16}")
    return keys


def _parent_keys_from_edge(edge: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    src_ext = _normalize_ext(edge.get("src_ext_address"))
    src_rloc = _normalize_rloc(edge.get("src_rloc16"))
    if src_ext:
        keys.add(f"ext:{src_ext}")
    if src_rloc:
        keys.add(f"rloc:{src_rloc}")
    return keys


def _child_key_from_edge(edge: dict[str, Any]) -> str | None:
    dst_ext = _normalize_ext(edge.get("dst_ext_address"))
    dst_rloc = _normalize_rloc(edge.get("dst_rloc16"))
    if dst_ext:
        return f"ext:{dst_ext}"
    if dst_rloc:
        return f"rloc:{dst_rloc}"
    return None


def _child_key_from_node(node: dict[str, Any]) -> str | None:
    ext_address = _normalize_ext(node.get("ext_address"))
    rloc16 = _normalize_rloc(node.get("rloc16"))
    if ext_address:
        return f"ext:{ext_address}"
    if rloc16:
        return f"rloc:{rloc16}"
    return None


def _infer_child_parent_matches(
    matter_nodes: list[dict[str, Any]],
    otbr_edges: list[dict[str, Any]],
    consumed_matter_exts: set[str],
    consumed_otbr_exts: set[str],
) -> dict[str, dict[str, Any]]:
    # Apply child association rules in strict order:
    # 1. Exact child RLOC under the same observed parent.
    # 2. Single quarantined child under a parent with one unresolved OTBR child.
    # 3. Single trusted residual child under a parent with one unresolved OTBR child.
    matter_by_parent: dict[str, list[dict[str, Any]]] = {}
    trusted_matter_by_parent: dict[str, list[dict[str, Any]]] = {}
    quarantined_matter_by_parent: dict[str, list[dict[str, Any]]] = {}
    trusted_rloc_candidates_by_parent: dict[str, dict[str, list[dict[str, Any]]]] = {}
    trusted_child_keys_by_parent: dict[str, set[str]] = {}
    for node in matter_nodes:
        if not node.get("available") or not _is_child_role(node):
            continue
        node_ext = _normalize_ext(node.get("ext_address"))
        if node_ext and node_ext in consumed_matter_exts:
            continue
        parent_keys = _observed_parent_keys(node)
        for key in parent_keys:
            matter_by_parent.setdefault(key, []).append(node)
            if node.get("address_trusted"):
                trusted_matter_by_parent.setdefault(key, []).append(node)
                node_rloc = _normalize_rloc(node.get("rloc16"))
                if node_rloc:
                    trusted_rloc_candidates_by_parent.setdefault(key, {}).setdefault(node_rloc, []).append(node)
                    trusted_child_keys_by_parent.setdefault(key, set()).add(f"rloc:{node_rloc}")
            else:
                quarantined_matter_by_parent.setdefault(key, []).append(node)

    child_edges_by_parent: dict[str, list[dict[str, Any]]] = {}
    for edge in otbr_edges:
        if edge.get("relation") != "parent_child":
            continue
        child_key = _child_key_from_edge(edge)
        if not child_key:
            continue
        child_ext = _normalize_ext(edge.get("dst_ext_address"))
        if child_ext and child_ext in consumed_otbr_exts:
            continue
        for parent_key in _parent_keys_from_edge(edge):
            child_edges_by_parent.setdefault(parent_key, []).append(edge)

    inferences: dict[str, dict[str, Any]] = {}
    inferred_matter_ids: set[str] = set()

    def infer_once(child_key: str, candidate: dict[str, Any], *, child_ext: str | None, child_rloc: str | None, matched_by: str) -> None:
        matter_id = candidate.get("matter_node_id")
        if matter_id is None or str(matter_id) in inferred_matter_ids or child_key in inferences:
            return
        inferences[child_key] = _child_inference_record(
            candidate,
            child_ext=child_ext,
            child_rloc=child_rloc,
            matched_by=matched_by,
        )
        inferred_matter_ids.add(str(matter_id))

    for parent_key, child_edges in child_edges_by_parent.items():
        for edge in child_edges:
            child_key = _child_key_from_edge(edge)
            child_rloc = _normalize_rloc(edge.get("dst_rloc16"))
            if not child_key or not child_rloc:
                continue
            rloc_candidates = (trusted_rloc_candidates_by_parent.get(parent_key) or {}).get(child_rloc) or []
            if len(rloc_candidates) == 1:
                infer_once(
                    child_key,
                    rloc_candidates[0],
                    child_ext=_normalize_ext(edge.get("dst_ext_address")),
                    child_rloc=child_rloc,
                    matched_by="same_rloc_and_parent_neighbor_evidence",
                )

    for parent_key, child_edges in child_edges_by_parent.items():
        unresolved_edges = [
            edge
            for edge in child_edges
            if (child_key := _child_key_from_edge(edge))
            and child_key not in inferences
            and child_key not in (trusted_child_keys_by_parent.get(parent_key) or set())
        ]
        unresolved_keys = {_child_key_from_edge(edge) for edge in unresolved_edges}
        unresolved_keys.discard(None)
        available_quarantined = [
            node
            for node in (quarantined_matter_by_parent.get(parent_key) or [])
            if str(node.get("matter_node_id")) not in inferred_matter_ids
        ]
        if len(available_quarantined) == 1 and len(unresolved_keys) == 1:
            child_edge = unresolved_edges[0]
            infer_once(
                next(iter(unresolved_keys)),
                available_quarantined[0],
                child_ext=_normalize_ext(child_edge.get("dst_ext_address")),
                child_rloc=_normalize_rloc(child_edge.get("dst_rloc16")),
                matched_by="quarantined_child_parent_evidence",
            )

    for parent_key, child_edges in child_edges_by_parent.items():
        unresolved_edges = [
            edge
            for edge in child_edges
            if (child_key := _child_key_from_edge(edge)) and child_key not in inferences
        ]
        unresolved_keys = {_child_key_from_edge(edge) for edge in unresolved_edges}
        unresolved_keys.discard(None)
        available_trusted = [
            node
            for node in (trusted_matter_by_parent.get(parent_key) or [])
            if str(node.get("matter_node_id")) not in inferred_matter_ids
        ]
        if len(available_trusted) != 1 or len(unresolved_keys) != 1:
            continue
        candidate = available_trusted[0]
        child_key = next(iter(unresolved_keys))
        candidate_key = _child_key_from_node(candidate)
        if not candidate_key or child_key == candidate_key:
            continue
        child_edge = unresolved_edges[0]
        infer_once(
            child_key,
            candidate,
            child_ext=_normalize_ext(child_edge.get("dst_ext_address")),
            child_rloc=_normalize_rloc(child_edge.get("dst_rloc16")),
            matched_by="unique_residual_parent_child",
        )

    return inferences


def _matter_neighbor_link_metrics(parent: dict[str, Any] | None, child: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(parent, dict) or not isinstance(child, dict):
        return None
    child_ext = _normalize_ext(child.get("ext_address"))
    child_rloc = _normalize_rloc(child.get("rloc16"))
    if not child_ext and not child_rloc:
        return None
    for link in parent.get("observed_neighbor_links") or []:
        if not isinstance(link, dict):
            continue
        link_ext = _normalize_ext(link.get("ext_address"))
        link_rloc = _normalize_rloc(link.get("rloc16"))
        if child_ext and link_ext and child_ext == link_ext:
            return link
        if child_rloc and link_rloc and child_rloc == link_rloc:
            return link
    return None


def _label_for_metrics(node: dict[str, Any] | None, fallback: str) -> str:
    if not isinstance(node, dict):
        return fallback
    return str(node.get("serial_number") or node.get("label") or node.get("ext_address") or node.get("rloc16") or fallback)


def _infer_router_neighbor_set_matches(
    matter_nodes: list[dict[str, Any]],
    otbr_nodes: list[dict[str, Any]],
    otbr_edges: list[dict[str, Any]],
    consumed_matter_exts: set[str],
    consumed_otbr_exts: set[str],
) -> dict[str, dict[str, Any]]:
    router_child_rlocs: dict[str, set[str]] = {}
    router_child_exts: dict[str, set[str]] = {}
    router_upstream_keys: dict[str, set[str]] = {}
    for edge in otbr_edges:
        src_ext = _normalize_ext(edge.get("src_ext_address"))
        src_rloc = _normalize_rloc(edge.get("src_rloc16"))
        dst_ext = _normalize_ext(edge.get("dst_ext_address"))
        dst_rloc = _normalize_rloc(edge.get("dst_rloc16"))
        parent_keys = {key for key in (f"ext:{src_ext}" if src_ext else None, f"rloc:{src_rloc}" if src_rloc else None) if key}
        dst_keys = {key for key in (f"ext:{dst_ext}" if dst_ext else None, f"rloc:{dst_rloc}" if dst_rloc else None) if key}
        if edge.get("relation") == "parent_child":
            for parent_key in parent_keys:
                if dst_rloc:
                    router_child_rlocs.setdefault(parent_key, set()).add(dst_rloc)
                if dst_ext:
                    router_child_exts.setdefault(parent_key, set()).add(dst_ext)
        elif edge.get("relation") == "neighbor":
            for src_key in parent_keys:
                router_upstream_keys.setdefault(src_key, set()).update(dst_keys)
            for dst_key in dst_keys:
                router_upstream_keys.setdefault(dst_key, set()).update(parent_keys)

    inferences: dict[str, dict[str, Any]] = {}
    for otbr_node in otbr_nodes:
        role = str(otbr_node.get("role") or "").strip().lower()
        if role not in {"leader", "router"}:
            continue
        otbr_ext = _normalize_ext(otbr_node.get("ext_address"))
        otbr_rloc = _normalize_rloc(otbr_node.get("rloc16"))
        if otbr_ext and otbr_ext in consumed_otbr_exts:
            continue
        router_keys = {key for key in (f"ext:{otbr_ext}" if otbr_ext else None, f"rloc:{otbr_rloc}" if otbr_rloc else None) if key}
        if not router_keys:
            continue
        child_rlocs = set().union(*(router_child_rlocs.get(key, set()) for key in router_keys))
        child_exts = set().union(*(router_child_exts.get(key, set()) for key in router_keys))
        upstream_keys = set().union(*(router_upstream_keys.get(key, set()) for key in router_keys))
        candidates: list[dict[str, Any]] = []
        for matter_node in matter_nodes:
            matter_id = matter_node.get("matter_node_id")
            matter_ext = _normalize_ext(matter_node.get("ext_address"))
            if matter_ext and matter_ext in consumed_matter_exts:
                continue
            if matter_id is not None and str(matter_id) in inferences:
                continue
            if not matter_node.get("available"):
                continue
            matter_role = str(matter_node.get("role") or "").strip().lower()
            if matter_role not in {"leader", "router", "reed"}:
                continue
            observed_exts = {_normalize_ext(value) for value in (matter_node.get("observed_neighbor_ext_addresses") or [])}
            observed_rlocs = {_normalize_rloc(value) for value in (matter_node.get("observed_neighbor_rloc16s") or [])}
            observed_keys = {f"ext:{value}" for value in observed_exts if value}
            observed_keys.update({f"rloc:{value}" for value in observed_rlocs if value})
            router_seen = bool(router_keys & observed_keys)
            child_overlap = len(child_rlocs & {value for value in observed_rlocs if value}) + len(child_exts & {value for value in observed_exts if value})
            upstream_seen = bool(upstream_keys & observed_keys)
            if router_seen and (child_overlap or upstream_seen):
                candidates.append(matter_node)
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        matter_id = candidate.get("matter_node_id")
        if matter_id is None:
            continue
        inferences[str(matter_id)] = {
            **candidate,
            "source": ["matter", "otbr"],
            "node_class": "matched_node",
            "role": otbr_node.get("role") or candidate.get("role"),
            "ext_address": otbr_ext,
            "rloc16": otbr_rloc,
            "matched": True,
            "matched_by": "router_neighbor_set_evidence",
            "match_rule": "router_neighbor_set_evidence",
            "inferred_match": True,
            "matter_ext_address": candidate.get("ext_address"),
            "matter_rloc16": candidate.get("rloc16"),
            "otbr_ext_address": otbr_ext,
            "otbr_rloc16": otbr_rloc,
        }
    return inferences




def build_thread_topology(
    matter_nodes: list[dict[str, Any]],
    thread_diag_snapshot: dict[str, Any],
    otbr_diag_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del thread_diag_snapshot  # The topology model is built from Matter Server inventory plus OTBR diagnostics.
    matter, warnings = _matter_nodes(matter_nodes)
    otbr = _parse_otbr(otbr_diag_snapshot)
    matter_by_ext = {node["ext_address"]: node for node in matter if node.get("ext_address") and node.get("address_trusted")}
    otbr_by_ext = {node["ext_address"]: node for node in otbr["nodes"] if node.get("ext_address")}
    merged_nodes: list[dict[str, Any]] = []
    consumed_matter_exts: set[str] = set()
    consumed_otbr_exts: set[str] = set()

    for ext_address in sorted(set(matter_by_ext) & set(otbr_by_ext)):
        matter_node = matter_by_ext[ext_address]
        otbr_node = otbr_by_ext[ext_address]
        merged_nodes.append(
            {
                **matter_node,
                "source": ["matter", "otbr"],
                "node_class": "matched_node",
                "role": otbr_node.get("role") or matter_node.get("role"),
                "rloc16": otbr_node.get("rloc16") or matter_node.get("rloc16"),
                "matched": True,
                "match_rule": "exact_ext_address",
            }
        )
        consumed_matter_exts.add(ext_address)
        consumed_otbr_exts.add(ext_address)

    for node in matter:
        if node.get("ext_address") in consumed_matter_exts:
            continue
        merged_nodes.append({**node, "matched": False})
        if node.get("available") and node.get("network_type") == "Thread" and node.get("address_trusted") and node.get("ext_address"):
            warnings.append(
                {
                    "type": "matter_node_unmatched_in_otbr",
                    "matter_node_id": node.get("matter_node_id"),
                    "serial_number": node.get("serial_number"),
                    "ext_address": node.get("ext_address"),
                    "rule": "no_exact_otbr_ext_match",
                }
            )

    for node in otbr["nodes"]:
        if node.get("ext_address") in consumed_otbr_exts:
            continue
        merged_nodes.append({**node, "matched": False})
        if node.get("ext_address"):
            warnings.append(
                {
                    "type": "otbr_node_unmatched_in_matter",
                    "ext_address": node.get("ext_address"),
                    "rloc16": node.get("rloc16"),
                    "role": node.get("role"),
                    "rule": "no_exact_matter_ext_match",
                }
            )

    inferred_nodes_by_matter_id: dict[str, dict[str, Any]] = _infer_router_neighbor_set_matches(
        matter,
        otbr.get("nodes") or [],
        otbr["edges"],
        consumed_matter_exts,
        consumed_otbr_exts,
    )
    child_inferences_by_key = _infer_child_parent_matches(
        matter,
        otbr["edges"],
        consumed_matter_exts,
        consumed_otbr_exts,
    )
    for inferred_node in child_inferences_by_key.values():
        matter_id = inferred_node.get("matter_node_id")
        if matter_id is None:
            continue
        inferred_nodes_by_matter_id[str(matter_id)] = inferred_node

    inferred_matter_ids = set(inferred_nodes_by_matter_id)
    inferred_child_keys: set[str] = set(child_inferences_by_key)
    for inferred_node in inferred_nodes_by_matter_id.values():
        inferred_key = _child_key_from_node(inferred_node)
        if inferred_key:
            inferred_child_keys.add(inferred_key)

    node_by_ext = {node.get("ext_address"): node for node in merged_nodes if node.get("ext_address")}
    node_by_rloc = {node.get("rloc16"): node for node in merged_nodes if node.get("rloc16") and not node.get("ext_address")}
    for inferred_node in inferred_nodes_by_matter_id.values():
        if inferred_node.get("ext_address"):
            node_by_ext[inferred_node.get("ext_address")] = inferred_node
        elif inferred_node.get("rloc16"):
            node_by_rloc[inferred_node.get("rloc16")] = inferred_node
    groups_by_parent: dict[str, dict[str, Any]] = {}
    for edge in otbr["edges"]:
        if edge.get("relation") != "parent_child":
            continue
        parent = None
        if otbr.get("otbr_node") and edge.get("src_ext_address") == otbr["otbr_node"].get("ext_address"):
            parent = otbr["otbr_node"]
        if not parent:
            parent = node_by_ext.get(edge.get("src_ext_address")) or node_by_rloc.get(edge.get("src_rloc16"))
        child = node_by_ext.get(edge.get("dst_ext_address")) or node_by_rloc.get(edge.get("dst_rloc16"))
        child_key = _child_key_from_edge(edge)
        inferred_child = child_inferences_by_key.get(child_key or "")
        if child and inferred_child and not child.get("matched"):
            child = {**child, "otbr_ext_address": child.get("ext_address"), "otbr_rloc16": child.get("rloc16"), **inferred_child}
        elif inferred_child:
            child = inferred_child
        parent_key = edge.get("src_ext_address") or edge.get("src_rloc16") or "unknown"
        group = groups_by_parent.setdefault(
            parent_key,
            {"parent": _summary(parent, str(parent_key)), "children": [], "_seen": set(), "_parent_node": parent},
        )
        relation_child_key = edge.get("dst_ext_address") or edge.get("dst_rloc16") or "unknown"
        entry = {
            "relation": "parent_child",
            "source": list(edge.get("source") or []),
            "confidence": edge.get("confidence"),
            "link_quality_in": edge.get("link_quality_in"),
            "link_quality_out": edge.get("link_quality_out"),
            "average_rssi_dbm": edge.get("average_rssi_dbm"),
            "last_rssi_dbm": edge.get("last_rssi_dbm"),
            "child": _summary(child, str(child_key)),
        }
        matter_link_metrics = _matter_neighbor_link_metrics(parent, child)
        if matter_link_metrics:
            entry["source"] = sorted(set(entry["source"] + ["matter"]))
            entry["rssi_source"] = "matter_neighbor_table"
            entry["rssi_observer_label"] = _label_for_metrics(parent, "parent")
            entry["rssi_target_label"] = _label_for_metrics(child, "child")
            for field in ("link_quality_in", "link_quality_out", "average_rssi_dbm", "last_rssi_dbm", "age_sec"):
                if entry.get(field) in (None, "") and matter_link_metrics.get(field) not in (None, ""):
                    entry[field] = matter_link_metrics.get(field)
        if relation_child_key in group["_seen"]:
            existing = next(
                (item for item in group["children"] if (item.get("child") or {}).get("ext_address") == edge.get("dst_ext_address") or (item.get("child") or {}).get("rloc16") == edge.get("dst_rloc16")),
                None,
            )
            if existing:
                existing["source"] = sorted(set((existing.get("source") or []) + entry["source"]))
                for field in ("link_quality_in", "link_quality_out", "average_rssi_dbm", "last_rssi_dbm", "age_sec", "rssi_source", "rssi_observer_label", "rssi_target_label", "confidence"):
                    if existing.get(field) in (None, "") and entry.get(field) not in (None, ""):
                        existing[field] = entry.get(field)
            continue
        group["_seen"].add(relation_child_key)
        group["children"].append(entry)

    otbr_node = otbr.get("otbr_node")
    if isinstance(otbr_node, dict):
        for edge in otbr["edges"]:
            if edge.get("relation") != "neighbor":
                continue
            src_is_otbr = _endpoint_matches_node(edge, "src", otbr_node)
            dst_is_otbr = _endpoint_matches_node(edge, "dst", otbr_node)
            if not (src_is_otbr or dst_is_otbr):
                continue
            neighbor_ext = edge.get("dst_ext_address") if src_is_otbr else edge.get("src_ext_address")
            neighbor_rloc = edge.get("dst_rloc16") if src_is_otbr else edge.get("src_rloc16")
            neighbor = node_by_ext.get(neighbor_ext) or node_by_rloc.get(neighbor_rloc)
            if not isinstance(neighbor, dict):
                continue
            role = str(neighbor.get("role") or "").strip().lower()
            if role not in {"leader", "router"}:
                continue
            neighbor_parent_key = neighbor.get("ext_address") or neighbor.get("rloc16")
            if neighbor_parent_key and neighbor_parent_key in groups_by_parent:
                continue
            parent_key = otbr_node.get("ext_address") or otbr_node.get("rloc16") or "otbr"
            group = groups_by_parent.setdefault(
                parent_key,
                {"parent": _summary(otbr_node, "OTBR / RCP"), "children": [], "_seen": set(), "_parent_node": otbr_node},
            )
            relation_child_key = neighbor.get("ext_address") or neighbor.get("rloc16") or "unknown"
            if relation_child_key in group["_seen"]:
                existing = next(
                    (
                        item
                        for item in group["children"]
                        if (item.get("child") or {}).get("ext_address") == neighbor.get("ext_address")
                        or (item.get("child") or {}).get("rloc16") == neighbor.get("rloc16")
                    ),
                    None,
                )
                if existing:
                    existing["source"] = sorted(set((existing.get("source") or []) + list(edge.get("source") or [])))
                    for field in ("link_quality_in", "link_quality_out", "average_rssi_dbm", "last_rssi_dbm", "confidence"):
                        if existing.get(field) in (None, "") and edge.get(field) not in (None, ""):
                            existing[field] = edge.get(field)
                continue
            group["_seen"].add(relation_child_key)
            group["children"].append(
                {
                    "relation": "neighbor",
                    "source": list(edge.get("source") or []),
                    "confidence": edge.get("confidence"),
                    "link_quality_in": edge.get("link_quality_in"),
                    "link_quality_out": edge.get("link_quality_out"),
                    "average_rssi_dbm": edge.get("average_rssi_dbm"),
                    "last_rssi_dbm": edge.get("last_rssi_dbm"),
                    "child": _summary(neighbor, str(relation_child_key)),
                }
            )

    groups = list(groups_by_parent.values())
    for group in groups:
        upstream = _upstream_otbr_link(group.get("_parent_node"), otbr.get("otbr_node"), otbr["edges"])
        if upstream:
            group["upstream"] = upstream
        group.pop("_seen", None)
        group.pop("_parent_node", None)
        group["children"].sort(key=lambda item: str((item.get("child") or {}).get("label") or ""))
    groups.sort(key=lambda item: str((item.get("parent") or {}).get("label") or ""))
    filtered_warnings = []
    for warning in warnings:
        if warning.get("type") == "matter_node_unmatched_in_otbr" and str(warning.get("matter_node_id")) in inferred_matter_ids:
            continue
        if warning.get("type") == "otbr_node_unmatched_in_matter":
            warning_key = _child_key_from_edge(
                {
                    "dst_ext_address": warning.get("ext_address"),
                    "dst_rloc16": warning.get("rloc16"),
                }
            )
            if warning_key in inferred_child_keys:
                continue
        filtered_warnings.append(warning)

    resolved_nodes: list[dict[str, Any]] = []
    for node in merged_nodes:
        matter_id = node.get("matter_node_id")
        if matter_id is not None and str(matter_id) in inferred_matter_ids:
            continue
        node_child_key = _child_key_from_node(node)
        if node_child_key and node_child_key in inferred_child_keys:
            continue
        resolved_nodes.append(node)
    resolved_nodes.extend(inferred_nodes_by_matter_id.values())
    sorted_nodes = sorted(
        resolved_nodes,
        key=lambda node: (0 if node.get("matched") else 1, str(node.get("serial_number") or node.get("ext_address") or node.get("rloc16") or "")),
    )

    return {
        "version": 2,
        "rules": [
            "ext_address is identity",
            "rloc16 is locator only",
            "duplicate available Matter ext_address is quarantined",
            "rloc-only OTBR children match only with unique Matter parent-neighbor evidence",
        ],
        "nodes": sorted_nodes,
        "edges": otbr["edges"],
        "tree": {"groups": groups, "parent_count": len(groups), "relation_count": sum(len(group.get("children") or []) for group in groups)},
        "warnings": filtered_warnings,
        "matter_inventory": matter,
        "observed_topology": {"otbr": otbr.get("otbr_node"), "nodes": otbr.get("nodes") or []},
        "counters": {
            "matter_nodes": len(matter),
            "available_matter_nodes": sum(1 for node in matter if node.get("available")),
            "trusted_matter_addresses": sum(1 for node in matter if node.get("address_trusted")),
            "otbr_nodes": len(otbr.get("nodes") or []) + (1 if otbr.get("otbr_node") else 0),
            "matched_nodes": sum(1 for node in sorted_nodes if node.get("matched")),
            "warnings": len(filtered_warnings),
            "tree_parents": len(groups),
            "tree_relations": sum(len(group.get("children") or []) for group in groups),
        },
    }
