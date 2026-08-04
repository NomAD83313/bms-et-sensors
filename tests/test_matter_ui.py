import re
import unittest

from app.matter.matter_ui import INDEX_HTML


class MatterUiTests(unittest.TestCase):
    def test_inline_script_avoids_known_compatibility_breakers(self) -> None:
        script = self._extract_script()

        self.assertIn("UI_BUILD_ID", script)
        self.assertNotIn("??", script)

    def test_unresolved_tree_rows_are_appended(self) -> None:
        script = self._extract_script()
        match = re.search(r"if \(unresolved\.length\) \{(?P<body>.*?)el\.innerHTML = blocks\.join", script, re.S)

        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        self.assertIn("blocks.push(`", body)
        self.assertNotIn("return `", body)

    def test_single_topology_frame_is_rendered(self) -> None:
        self.assertIn("Matter over Thread", INDEX_HTML)
        self.assertIn('id="topologySummary"', INDEX_HTML)
        self.assertIn('id="threadOtbrMaster"', INDEX_HTML)
        self.assertIn('id="topologyTree"', INDEX_HTML)
        self.assertIn('id="topologyNodes"', INDEX_HTML)
        self.assertIn('id="topologyWarnings"', INDEX_HTML)
        self.assertNotIn('class="topology-compare"', INDEX_HTML)

        script = self._extract_script()
        self.assertIn('fetch("./thread-topology"', script)
        self.assertIn("renderTopologySummary(topology)", script)
        self.assertIn("function renderThreadMaster", script)
        self.assertIn("renderThreadMaster(topology, (otData && otData.diag) || {})", script)
        self.assertIn("renderTopologyTree(topology.tree || {})", script)
        self.assertIn("renderKnownMatterDevices(topology.matter_inventory || [], topology.tree || {}, matterSnapshotPending)", script)
        self.assertIn("renderTopologyWarnings(matterSnapshotPending ? [] : (topology.warnings || []))", script)

    def test_wifi_matter_frame_is_rendered(self) -> None:
        self.assertIn("Matter over Wi-Fi", INDEX_HTML)
        self.assertIn('id="wifiApMaster"', INDEX_HTML)
        self.assertIn('id="wifiMatterNodes"', INDEX_HTML)
        self.assertIn('href="/ap/api/status"', INDEX_HTML)

        script = self._extract_script()
        self.assertIn('fetch("/ap/api/status"', script)
        self.assertIn("function renderWifiMaster", script)
        self.assertIn("function renderWifiMatterDevices", script)
        self.assertIn("function wifiSignalClass", script)
        self.assertIn("renderWifiMatterDevices(topology.matter_inventory || [], apStatus, matterSnapshotPending)", script)

    def test_standard_matter_controls_are_rendered_from_inventory(self) -> None:
        script = self._extract_script()

        self.assertIn("function renderMatterControlButtons", script)
        self.assertIn("node.standard_controls", script)
        self.assertIn("matter-command-btn", script)
        self.assertIn("./nodes/${encodeURIComponent(nodeId)}/commands", script)
        self.assertIn("endpoint_id: Number(button.dataset.endpointId)", script)
        self.assertIn("cluster_id: Number(button.dataset.clusterId)", script)
        self.assertIn("command_name: button.dataset.commandName", script)

    def test_topology_parent_groups_render_matter_controls(self) -> None:
        script = self._extract_script()
        nested = re.search(r"function renderNestedTopologyGroup(?P<body>.*?)function renderTopologyTree", script, re.S)
        tree = re.search(r"function renderTopologyTree(?P<body>.*?)if \(unresolved\.length\)", script, re.S)

        self.assertIsNotNone(nested)
        self.assertIsNotNone(tree)
        self.assertIn("const parentActions = renderMatterControlButtons(parent)", nested.group("body") if nested else "")
        self.assertIn("${parentActions}", nested.group("body") if nested else "")
        self.assertIn("const parentActions = renderMatterControlButtons(parent)", tree.group("body") if tree else "")
        self.assertIn("${parentActions}", tree.group("body") if tree else "")

    def test_topology_tree_actions_use_full_width_rows(self) -> None:
        self.assertIn(".tree-parent > .service-actions", INDEX_HTML)
        self.assertIn(".tree-child-row > .service-actions", INDEX_HTML)
        self.assertIn("grid-column: 1 / -1", INDEX_HTML)

    def test_air_reboot_control_is_rendered_from_inventory(self) -> None:
        script = self._extract_script()

        self.assertIn("node.air_reboot_supported", script)
        self.assertIn("matter-air-reboot-btn", script)
        self.assertIn("./nodes/${encodeURIComponent(nodeId)}/air-reboot", script)
        self.assertIn("function invokeMatterAirReboot", script)

    def test_runtime_diagnostics_are_rendered_from_inventory(self) -> None:
        script = self._extract_script()

        self.assertIn("function runtimeMeta", script)
        self.assertIn("node.uptime_sec", script)
        self.assertIn("node.estimated_last_boot_at", script)
        self.assertIn("last boot ~", script)
        self.assertIn("node.reboot_count", script)
        self.assertIn("node.boot_reason_label", script)

    def test_wifi_matter_frame_avoids_thread_address_artifacts(self) -> None:
        script = self._extract_script()
        match = re.search(r"function renderWifiMatterDevices(?P<body>.*?)function renderThreadMaster", script, re.S)

        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        self.assertIn('stateBadge("wifi", "status-muted")', body)
        self.assertIn("nodeBadge(node.matter_node_id)", body)
        self.assertIn('stateBadge("matched", "status-ok")', body)
        self.assertIn('stateBadge("unmatched", "status-warn")', body)
        self.assertIn("wifiRssiBadge(apClient)", body)
        self.assertIn("wifiQualityBadge(apClient)", body)
        self.assertIn("wifiSignalClass(client)", script)
        self.assertIn("function rssiSignalClass", script)
        self.assertIn("client.signal_dbm", script)
        self.assertIn("client.signal_quality", script)
        self.assertIn("rssi ${client.signal_dbm} dBm", script)
        self.assertIn("rssiSignalClass(client.signal_dbm)", script)
        self.assertIn("quality ${client.signal_quality}%", script)
        self.assertIn("network WiFi", body)
        self.assertNotIn("address conflict", body)
        self.assertNotIn("reported ext", body)
        self.assertNotIn("rloc", body)
        self.assertNotIn("rssi ${esc", body)
        self.assertNotIn("quality ${esc", body)

    def test_wifi_frame_unescapes_nmcli_mac_addresses(self) -> None:
        script = self._extract_script()
        master = re.search(r"function renderWifiMaster(?P<body>.*?)function renderWifiMatterDevices", script, re.S)
        devices = re.search(r"function renderWifiMatterDevices(?P<body>.*?)function renderThreadMaster", script, re.S)

        self.assertIsNotNone(master)
        self.assertIsNotNone(devices)
        self.assertIn("function displayMac", script)
        self.assertIn("replaceAll", script)
        self.assertIn("displayMac(ap.mac_address)", master.group("body") if master else "")
        self.assertIn("displayMac(apClient.mac)", devices.group("body") if devices else "")

    def test_topology_badges_have_static_token_colors(self) -> None:
        script = self._extract_script()

        self.assertIn("function badgeTokenClass", script)
        self.assertIn("${badgeTokenClass(label)}", script)
        self.assertIn(".topology-badge.badge-matter", INDEX_HTML)
        self.assertIn(".topology-badge.badge-wifi", INDEX_HTML)
        self.assertIn(".topology-badge.badge-rssi", INDEX_HTML)
        self.assertIn(".topology-badge.badge-rloc", INDEX_HTML)
        self.assertIn(".topology-badge.badge-parent-child", INDEX_HTML)
        self.assertNotIn("linear-gradient(135deg, #eaf2ff", INDEX_HTML)

    def test_topology_links_frame_is_not_rendered(self) -> None:
        self.assertNotIn("Topology Links", INDEX_HTML)
        self.assertNotIn('id="topologyEdges"', INDEX_HTML)
        self.assertNotIn("renderTopologyEdges", INDEX_HTML)

    def test_topology_counters_frame_is_not_rendered(self) -> None:
        self.assertNotIn('id="topologyCounters"', INDEX_HTML)
        self.assertNotIn("renderTopologyCounters", INDEX_HTML)

    def test_topology_tree_shows_explicit_thread_identifiers(self) -> None:
        script = self._extract_script()

        self.assertIn("function threadIdMeta", script)
        self.assertIn("function nodeBadge", script)
        self.assertIn("node-id", INDEX_HTML)
        self.assertIn("node ${nodeId}", script)
        self.assertIn("ext ${esc(record.ext_address || \"unknown\")}", script)
        self.assertIn("rloc ${esc(record.rloc16 || \"unknown\")}", script)
        self.assertIn("<span>neighbor link</span>", script)

    def test_topology_tree_nests_groups_under_shared_upstream_parent(self) -> None:
        script = self._extract_script()

        self.assertIn("function nestTopologyGroups", script)
        self.assertIn("function renderNestedTopologyGroup", script)
        self.assertIn("parentWrapper.nested.push(wrapper)", script)

    def test_topology_tree_renders_rssi_with_observer_context(self) -> None:
        script = self._extract_script()

        self.assertIn("function rssiMetrics", script)
        self.assertIn("rssi last ${esc(item.last_rssi_dbm)} dBm", script)
        self.assertIn("${esc(item.rssi_observer_label)} hears ${esc(item.rssi_target_label)}", script)
        self.assertIn("...rssiMetrics(item)", script)
        self.assertIn("function upstreamMetrics", script)
        self.assertIn("upstream ${esc(upstreamNode.label", script)
        self.assertIn("...upstreamMetrics(upstream)", script)

    def test_thread_badge_area_keeps_rloc_and_rssi(self) -> None:
        script = self._extract_script()

        self.assertIn("function rlocBadge", script)
        self.assertIn("function threadRssiBadge", script)
        self.assertIn("rssiSignalClass(value)", script)
        self.assertIn("badges.push(rlocBadge(otbrRloc))", script)
        self.assertIn("badges.push(rlocBadge(parent))", script)
        self.assertIn("badges.push(threadRssiBadge(upstream))", script)
        self.assertIn("childBadges.push(rlocBadge(child))", script)
        self.assertIn("childBadges.push(threadRssiBadge(item))", script)
        self.assertIn("upstreamBadges.push(rlocBadge(upstreamNode))", script)
        self.assertIn("upstreamBadges.push(threadRssiBadge(upstream))", script)

    def test_thread_tree_does_not_duplicate_otbr_master_card(self) -> None:
        script = self._extract_script()

        self.assertIn('upstream.node.node_class !== "otbr"', script)
        self.assertIn("renderThreadMaster(topology, (otData && otData.diag) || {})", script)

    def test_known_devices_uses_matter_inventory_and_keeps_offline_nodes_visible(self) -> None:
        script = self._extract_script()

        self.assertIn("function renderKnownMatterDevices", script)
        self.assertIn("topology.matter_inventory || []", script)
        self.assertIn("if (node.available === false) return true;", script)
        self.assertIn('if (networkType !== "thread") return false;', script)
        self.assertIn("network ${esc(networkType)}", script)
        self.assertIn("Matter inventory refresh pending...", script)
        self.assertIn("topologyData.matter_snapshot_pending", script)

    def test_topology_warnings_include_link_context(self) -> None:
        script = self._extract_script()

        self.assertIn("warning.relation", script)
        self.assertIn("warning.src_label", script)
        self.assertIn("warning.src_ext_address", script)
        self.assertIn("warning.src_rloc16", script)
        self.assertIn("warning.dst_label", script)
        self.assertIn("warning.dst_ext_address", script)
        self.assertIn("warning.dst_rloc16", script)
        self.assertIn("warning.signal_reasons", script)

    def test_otbr_diag_uses_local_route_without_svcctl_fallback(self) -> None:
        script = self._extract_script()

        self.assertIn('fetch("./openthread/diag"', script)
        self.assertNotIn('fetch("/api/control/services/openthread/diag"', script)
        self.assertIn('setOtStatus("degraded", "diag unavailable")', script)

    def test_otbr_diag_switches_rcp_bridge_labels(self) -> None:
        script = self._extract_script()

        self.assertIn('id="otDongleLabel"', INDEX_HTML)
        self.assertIn('id="otUsbLabel"', INDEX_HTML)
        self.assertIn('transport.kind === "network_rcp_bridge"', script)
        self.assertIn('setText("otDongleLabel", isNetworkRcp ? "RCP Transport" : "Dongle")', script)
        self.assertIn('setText("otUsbLabel", isNetworkRcp ? "RCP Endpoint" : "USB VID:PID")', script)
        self.assertIn('nvl(transport.label, "WLAN RCP bridge")', script)

    def test_thread_credentials_ui_shows_local_command_without_secret_payload(self) -> None:
        script = self._extract_script()

        self.assertIn("Thread Credentials", INDEX_HTML)
        self.assertIn("credentials hidden from UI", INDEX_HTML)
        self.assertIn("docker exec openthread-border-router ot-ctl dataset active -x", INDEX_HTML)
        self.assertIn('id="otNetworkName"', INDEX_HTML)
        self.assertIn('id="otExtAddr"', INDEX_HTML)
        self.assertIn('id="otRloc16"', INDEX_HTML)
        self.assertIn('setText("otNetworkName", nvl(settings.network_name, "-"))', script)
        self.assertIn('setText("otExtAddr", nvl(settings.extaddr, "-"))', script)
        self.assertNotIn("dataset_tlv", INDEX_HTML)

    def test_matter_stack_cards_are_rendered_in_console(self) -> None:
        self.assertIn("Matter Stack", INDEX_HTML)
        self.assertIn('id="otbrServiceStatus"', INDEX_HTML)
        self.assertIn('id="matterServerStatus"', INDEX_HTML)
        self.assertIn('id="matterConsoleStatus"', INDEX_HTML)
        self.assertIn('id="restartOtbr"', INDEX_HTML)
        self.assertIn('id="restartMatterServer"', INDEX_HTML)

        script = self._extract_script()
        self.assertIn('fetch("./control/matter-server/health"', script)
        self.assertIn('setStackService("matterConsoleStatus"', script)

    def _extract_script(self) -> str:
        match = re.search(r"<script>(?P<script>.*)</script>", INDEX_HTML, re.S)
        self.assertIsNotNone(match)
        return match.group("script") if match else ""


if __name__ == "__main__":
    unittest.main()
