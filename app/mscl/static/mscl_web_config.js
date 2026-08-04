    const LAST_COMM_WARN_SEC = 30;
    const LAST_COMM_CRITICAL_SEC = 120;
    let idleConfirmed = false;
    let readConfirmed = false;
    let writeCaps = {};
    let currentRtdSensorOptions = [];
    let currentThermistorSensorOptions = [];
    let currentThermocoupleSensorOptions = [];
    let currentRtdWireOptions = [];

    function setOverviewValue(id, value) {
        const el = document.getElementById(id);
        if (el) el.innerText = value;
    }

    function currentSelectedNodeLabel() {
        const nodeSel = document.getElementById('nodeId');
        return nodeSel ? `Node ${nodeSel.value}` : "Node -";
    }

    function syncOverviewReadiness() {
        if (!readConfirmed) {
            setOverviewValue('overviewReadiness', 'Awaiting node read');
            return;
        }
        if (idleConfirmed) {
            setOverviewValue('overviewReadiness', 'Ready to write');
            return;
        }
        setOverviewValue('overviewReadiness', 'Node busy or not idle');
    }

    function resetNodeOverview() {
        setOverviewValue('overviewNodeId', currentSelectedNodeLabel());
        setOverviewValue('overviewReadiness', 'Awaiting node read');
        setOverviewValue('overviewLastRead', 'Not loaded yet');
        setOverviewValue('overviewNodeState', 'Unknown');
        setOverviewValue('overviewLastComm', 'Unavailable');
    }

    function updateReadWriteButtons() {
        const btnRead = document.getElementById('btnRead');
        const btnWrite = document.getElementById('btnWrite');
        if (btnRead) btnRead.disabled = !idleConfirmed;
        if (btnWrite) btnWrite.disabled = (!idleConfirmed || !readConfirmed);
    }

    function markIdleState(isIdle) {
        idleConfirmed = !!isIdle;
        updateReadWriteButtons();
        syncOverviewReadiness();
    }

    function markReadState(isRead) {
        readConfirmed = !!isRead;
        updateReadWriteButtons();
        syncOverviewReadiness();
    }

    function formatLocalTimestamp(dateObj) {
        const dt = dateObj instanceof Date ? dateObj : new Date();
        const yyyy = dt.getFullYear();
        const mm = String(dt.getMonth() + 1).padStart(2, "0");
        const dd = String(dt.getDate()).padStart(2, "0");
        const hh = String(dt.getHours()).padStart(2, "0");
        const mi = String(dt.getMinutes()).padStart(2, "0");
        const ss = String(dt.getSeconds()).padStart(2, "0");
        return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
    }

    function updateSampleRateHint() {
        const hint = document.getElementById('sampleRateHint');
        const lpSel = document.getElementById('lowPassFilter');
        if (!hint || !lpSel) return;
        const label = (lpSel.options[lpSel.selectedIndex]?.text || "").toLowerCase();
        if (!label || label === "n/a") {
            hint.innerText = "Read node to populate constraints.";
            return;
        }
        if (label.includes("2.6")) {
            hint.innerText = "LPF 2.6 Hz: max sample rate 2 Hz.";
            return;
        }
        if (label.includes("12.66")) {
            hint.innerText = "LPF 12.66 Hz: max sample rate 8 Hz.";
            return;
        }
        if (label.includes("294")) {
            hint.innerText = "LPF 294 Hz: sample rate not capped by LPF.";
            return;
        }
        hint.innerText = "Check LPF vs sample rate compatibility.";
    }

    function renderHardwareDependentOptions() {
        const trSel = document.getElementById('transducerType');
        const sensorSel = document.getElementById('sensorType');
        const wireSel = document.getElementById('wireType');
        if (!trSel || !sensorSel || !wireSel) return;
        const transducerVal = parseInt(trSel.value || "", 10);

        sensorSel.innerHTML = "";
        let sensorOptions = [];
        if (transducerVal === 0) sensorOptions = currentThermocoupleSensorOptions;
        else if (transducerVal === 1) sensorOptions = currentRtdSensorOptions;
        else if (transducerVal === 2) sensorOptions = currentThermistorSensorOptions;
        if (!sensorOptions.length) {
            const opt = document.createElement('option');
            opt.value = "";
            opt.text = "N/A";
            opt.selected = true;
            sensorSel.add(opt);
        } else {
            const prev = sensorSel.getAttribute('data-current') || "";
            sensorOptions.forEach(r => {
                const opt = document.createElement('option');
                opt.value = r.value;
                opt.text = r.label || `Value ${r.value}`;
                if (String(r.value) === String(prev)) opt.selected = true;
                sensorSel.add(opt);
            });
        }

        wireSel.innerHTML = "";
        let wireOptions = [];
        if (transducerVal === 1) wireOptions = currentRtdWireOptions;
        if (!wireOptions.length) {
            const opt = document.createElement('option');
            opt.value = "";
            opt.text = "N/A";
            opt.selected = true;
            wireSel.add(opt);
            wireSel.disabled = true;
        } else {
            const prev = wireSel.getAttribute('data-current') || "";
            wireOptions.forEach(r => {
                const opt = document.createElement('option');
                opt.value = r.value;
                opt.text = r.label || `Value ${r.value}`;
                if (String(r.value) === String(prev)) opt.selected = true;
                wireSel.add(opt);
            });
            wireSel.disabled = false;
        }
    }

    async function connectBase() {
        try {
            let res = await fetch('api/connect', {method:'POST'});
            let data = await res.json();
            if (!data.success) {
                res = await fetch('api/reconnect', {method:'POST'});
                data = await res.json();
            }
            const statusDiv = document.getElementById('baseActionStatus');
            if (data.success) {
                statusDiv.className = "small fw-bold status-ok mb-2";
                statusDiv.innerHTML = "Base connected";
                markIdleState(false);
            } else {
                statusDiv.className = "small fw-bold status-err mb-2";
                statusDiv.innerHTML = `Base connect failed: ${data.message || data.error || 'Unknown error'}`;
                markIdleState(false);
            }
            await refreshBaseStatus();
        } catch (e) { }
    }

    async function readConfig() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const metaDiv = document.getElementById('readMeta');
        const btnRead = document.getElementById('btnRead');
        const wasIdle = idleConfirmed;
        btnRead.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "Reading...";
        setOverviewValue('overviewNodeId', `Node ${id}`);
        setOverviewValue('overviewNodeState', 'Reading configuration...');
        if (metaDiv) metaDiv.innerText = "";
        document.getElementById('configCard').style.display = "none";
        
        try {
            const res = await fetch(`api/read/${id}`);
            const data = await res.json();
            if(data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "Read complete";
                const loadedAt = formatLocalTimestamp(new Date());
                if (metaDiv) metaDiv.innerText = `Loaded from node at ${loadedAt}`;
                setOverviewValue('overviewLastRead', loadedAt);
                setOverviewValue(
                    'overviewNodeState',
                    data.state_text
                        ? `${data.state_text}${(data.state !== null && data.state !== undefined) ? ` (${data.state})` : ''}`
                        : 'Read complete'
                );
                document.getElementById('configCard').style.display = "block";
                
                const txSel = document.getElementById('txPower');
                txSel.innerHTML = "";
                const txOptions = Array.isArray(data.tx_power_options) ? data.tx_power_options : [];
                if (!txOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    txSel.add(opt);
                    txSel.disabled = true;
                } else {
                    txSel.disabled = false;
                    txOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `${r.value} dBm`;
                        if (String(r.value) === String(data.current_power)) opt.selected = true;
                        txSel.add(opt);
                    });
                    if (txSel.selectedIndex < 0 && data.current_power !== null && data.current_power !== undefined) {
                        const cur = document.createElement('option');
                        cur.value = data.current_power;
                        cur.text = `${data.current_power} dBm`;
                        cur.selected = true;
                        txSel.add(cur);
                    }
                }
                const inputSel = document.getElementById('inputRange');
                inputSel.innerHTML = "";
                const inputRanges = Array.isArray(data.supported_input_ranges) ? data.supported_input_ranges : [];
                if (!inputRanges.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    inputSel.add(opt);
                } else {
                    inputRanges.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (r.primary) opt.style.fontWeight = "700";
                        if (String(r.value) === String(data.current_input_range)) opt.selected = true;
                        inputSel.add(opt);
                    });
                }
                const selectedInputRangeOpt = inputSel.options[inputSel.selectedIndex];
                const inputRangeActual = selectedInputRangeOpt ? selectedInputRangeOpt.text : "N/A";

                const unitSel = document.getElementById('calibrationUnit');
                unitSel.innerHTML = "";
                const unitOptions = Array.isArray(data.unit_options) ? data.unit_options : [];
                if (!unitOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    unitSel.add(opt);
                } else {
                    unitOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_unit)) opt.selected = true;
                        unitSel.add(opt);
                    });
                }
                unitSel.disabled = unitOptions.length === 0;
                const cjcUnitSel = document.getElementById('cjcCalibrationUnit');
                cjcUnitSel.innerHTML = "";
                const cjcUnitOptions = Array.isArray(data.cjc_unit_options) ? data.cjc_unit_options : [];
                if (!cjcUnitOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    cjcUnitSel.add(opt);
                } else {
                    cjcUnitOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_cjc_unit)) opt.selected = true;
                        cjcUnitSel.add(opt);
                    });
                }
                cjcUnitSel.disabled = cjcUnitOptions.length === 0;

                const lpSel = document.getElementById('lowPassFilter');
                lpSel.innerHTML = "";
                const lpOptions = Array.isArray(data.low_pass_options) ? data.low_pass_options : [];
                if (!lpOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    lpSel.add(opt);
                } else {
                    lpOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_low_pass)) opt.selected = true;
                        lpSel.add(opt);
                    });
                }
                lpSel.onchange = updateSampleRateHint;

                const smSel = document.getElementById('storageLimitMode');
                smSel.innerHTML = "";
                const smOptions = Array.isArray(data.storage_limit_options) ? data.storage_limit_options : [];
                if (!smOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    smSel.add(opt);
                } else {
                    smOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_storage_limit_mode)) opt.selected = true;
                        smSel.add(opt);
                    });
                }
                const dmSelSampling = document.getElementById('dataMode');
                dmSelSampling.innerHTML = "";
                const dmOptionsSampling = Array.isArray(data.data_mode_options) ? data.data_mode_options : [];
                if (!dmOptionsSampling.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    dmSelSampling.add(opt);
                    dmSelSampling.disabled = true;
                } else {
                    dmSelSampling.disabled = false;
                    dmOptionsSampling.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_data_mode)) opt.selected = true;
                        dmSelSampling.add(opt);
                    });
                }
                document.getElementById('lostBeaconTimeout').value = (data.current_lost_beacon_timeout ?? 2);
                document.getElementById('diagnosticInterval').value = (data.current_diagnostic_interval ?? 60);
                const lostBeaconEnabled = !!data.current_lost_beacon_enabled;
                const diagnosticEnabled = !!data.current_diagnostic_enabled;
                document.getElementById('lostBeaconEnabled').checked = lostBeaconEnabled;
                document.getElementById('diagnosticEnabled').checked = diagnosticEnabled;
                document.getElementById('lostBeaconTimeout').disabled = !lostBeaconEnabled;
                document.getElementById('diagnosticInterval').disabled = !diagnosticEnabled;
                const supportsDefaultMode = !!data.supports_default_mode;
                const supportsInactivityTimeout = !!data.supports_inactivity_timeout;
                const supportsCheckRadioInterval = !!data.supports_check_radio_interval;
                const supportsTransducerType = !!data.supports_transducer_type;
                const supportsTempSensorOptions = !!data.supports_temp_sensor_options;
                const supportsInputRange = inputRanges.length > 0;
                const supportsLowPassFilter = lpOptions.length > 0;
                const supportsStorageLimitMode = smOptions.length > 0;
                const supportsDataMode = dmOptionsSampling.length > 0;
                const supportsCalibrationUnit = unitOptions.length > 0;
                const supportsCjcUnit = cjcUnitOptions.length > 0;
                writeCaps = {
                    supportsDefaultMode,
                    supportsInactivityTimeout,
                    supportsCheckRadioInterval,
                    supportsTransducerType,
                    supportsTempSensorOptions,
                    supportsInputRange,
                    supportsLowPassFilter,
                    supportsStorageLimitMode,
                    supportsDataMode,
                    supportsCalibrationUnit,
                    supportsCjcUnit
                };
                const trSel = document.getElementById('transducerType');
                trSel.innerHTML = "";
                const trOptions = Array.isArray(data.transducer_options) ? data.transducer_options : [];
                if (!trOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    trSel.add(opt);
                } else {
                    trOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_transducer_type)) opt.selected = true;
                        trSel.add(opt);
                    });
                }
                trSel.disabled = !supportsTransducerType;
                trSel.onchange = renderHardwareDependentOptions;
                document.getElementById('transducerTypeWrap').style.display = supportsTransducerType ? "" : "none";
                document.getElementById('sensorTypeWrap').style.display = supportsTempSensorOptions ? "" : "none";
                document.getElementById('wireTypeWrap').style.display = supportsTempSensorOptions ? "" : "none";
                document.getElementById('inputRangeWrap').style.display = supportsInputRange ? "" : "none";
                document.getElementById('lowPassFilterWrap').style.display = supportsLowPassFilter ? "" : "none";
                document.getElementById('calibrationUnitWrap').style.display = supportsCalibrationUnit ? "" : "none";
                document.getElementById('cjcCalibrationUnitWrap').style.display = supportsCjcUnit ? "" : "none";
                document.getElementById('dataModeWrap').style.display = supportsDataMode ? "" : "none";
                document.getElementById('storageLimitModeWrap').style.display = supportsStorageLimitMode ? "" : "none";

                currentRtdSensorOptions = Array.isArray(data.rtd_sensor_options) ? data.rtd_sensor_options : [];
                currentThermistorSensorOptions = Array.isArray(data.thermistor_sensor_options) ? data.thermistor_sensor_options : [];
                currentThermocoupleSensorOptions = Array.isArray(data.thermocouple_sensor_options) ? data.thermocouple_sensor_options : [];
                currentRtdWireOptions = Array.isArray(data.rtd_wire_options) ? data.rtd_wire_options : [];
                const sensorSel = document.getElementById('sensorType');
                const wireSel = document.getElementById('wireType');
                sensorSel.setAttribute('data-current', data.current_sensor_type ?? "");
                wireSel.setAttribute('data-current', data.current_wire_type ?? "");
                sensorSel.disabled = !supportsTempSensorOptions;
                wireSel.disabled = !supportsTempSensorOptions;
                renderHardwareDependentOptions();

                const dmWrap = document.getElementById('defaultModeWrap');
                const dmSel = document.getElementById('defaultMode');
                dmSel.innerHTML = "";
                const dmOptions = Array.isArray(data.default_mode_options) ? data.default_mode_options : [];
                if (supportsDefaultMode && dmOptions.length) {
                    dmWrap.style.display = "";
                    dmSel.disabled = false;
                    dmOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_default_mode)) opt.selected = true;
                        dmSel.add(opt);
                    });
                } else {
                    dmWrap.style.display = "none";
                    dmSel.disabled = true;
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    dmSel.add(opt);
                }

                const itWrap = document.getElementById('inactivityTimeoutWrap');
                const itInput = document.getElementById('inactivityTimeout');
                if (supportsInactivityTimeout) {
                    itWrap.style.display = "";
                    itInput.disabled = false;
                    itInput.value = (data.current_inactivity_timeout ?? 30);
                    const inactivityEnabled = !!data.current_inactivity_enabled;
                    document.getElementById('inactivityEnabled').checked = inactivityEnabled;
                    itInput.disabled = !inactivityEnabled;
                } else {
                    itWrap.style.display = "none";
                    itInput.disabled = true;
                    itInput.value = "";
                    document.getElementById('inactivityEnabled').checked = false;
                }

                const criWrap = document.getElementById('checkRadioIntervalWrap');
                const criInput = document.getElementById('checkRadioInterval');
                if (supportsCheckRadioInterval) {
                    criWrap.style.display = "";
                    criInput.disabled = false;
                    criInput.value = (data.current_check_radio_interval ?? 5);
                } else {
                    criWrap.style.display = "none";
                    criInput.disabled = true;
                    criInput.value = "";
                }
                const activeChannelsText = Array.isArray(data.channels)
                    ? data.channels.filter(ch => ch.enabled).map(ch => `ch${ch.id}`).join(", ")
                    : "";
                const stateNonIdle = (data.state !== null && data.state !== undefined) && String(data.state) !== "0";
                const stateText = data.state_text
                    ? `State: ${data.state_text}${(data.state !== null && data.state !== undefined) ? ` (${data.state})` : ''}`
                    : null;
                const lastCommDisplay = formatNodeLastCommLocal(data.last_comm);
                const lastCommAgeSec = parseLastCommAgeSec(data.last_comm);
                const lastCommWarn = Number.isFinite(lastCommAgeSec) && lastCommAgeSec > LAST_COMM_WARN_SEC && lastCommAgeSec <= LAST_COMM_CRITICAL_SEC;
                const lastCommDanger = Number.isFinite(lastCommAgeSec) && lastCommAgeSec > LAST_COMM_CRITICAL_SEC;
                const lastCommText = lastCommDisplay
                    ? (lastCommDanger
                        ? `<span class='status-err'>Last Comm: ${lastCommDisplay} (${Math.round(lastCommAgeSec)}s ago)</span>`
                        : (lastCommWarn
                            ? `<span class='status-warn'>Last Comm: ${lastCommDisplay} (${Math.round(lastCommAgeSec)}s ago)</span>`
                            : `Last Comm: ${lastCommDisplay}`))
                    : null;
                const storagePctNum = (data.storage_pct !== null && data.storage_pct !== undefined)
                    ? Number(data.storage_pct)
                    : null;
                const storageDanger = Number.isFinite(storagePctNum) && storagePctNum > 90;
                const storageWarn = Number.isFinite(storagePctNum) && storagePctNum >= 70 && storagePctNum <= 90;
                const statusParts = [
                    `Model: ${data.model}`,
                    `FW: ${data.fw}`,
                    (data.node_address !== null && data.node_address !== undefined) ? `Node Address: ${data.node_address}` : null,
                    lastCommText,
                    stateText ? (stateNonIdle ? `<span class='status-warn'>${stateText}</span>` : stateText) : null,
                    data.comm_protocol_text ? `Comm Protocol: ${data.comm_protocol_text}${(data.comm_protocol !== null && data.comm_protocol !== undefined) ? ` (${data.comm_protocol})` : ''}` : null,
                    data.region ? `Region: ${data.region}` : null,
                    data.frequency ? `Frequency: ${data.frequency}` : null,
                    (data.current_power !== null && data.current_power !== undefined) ? `Transmit Power: ${data.current_power} dBm` : null,
                    inputRangeActual ? `Input Range: ${inputRangeActual}` : null,
                    activeChannelsText ? `Active Channels: ${activeChannelsText}` : null,
                    data.sampling_mode ? `Sampling: ${data.sampling_mode}` : null,
                    (data.current_data_mode !== null && data.current_data_mode !== undefined)
                        ? `Data Mode: ${data.data_mode_text || ('Value ' + data.current_data_mode)} (${data.current_data_mode})`
                        : null,
                    (data.storage_capacity_raw !== null && data.storage_capacity_raw !== undefined)
                        ? `Storage Capacity: ${formatStorageCapacity(data.storage_capacity_raw)}`
                        : null,
                    (storagePctNum !== null)
                        ? (storageDanger
                            ? `<span class='status-err'>Storage: ${storagePctNum}%</span>`
                            : (storageWarn
                                ? `<span class='status-warn'>Storage: ${storagePctNum}%</span>`
                                : `Storage: ${storagePctNum}%`))
                        : null
                ].filter(Boolean);
                setOverviewValue(
                    'overviewLastComm',
                    lastCommDisplay
                        ? `${lastCommDisplay}${Number.isFinite(lastCommAgeSec) ? ` (${Math.round(lastCommAgeSec)}s ago)` : ''}`
                        : 'Unavailable'
                );
                document.getElementById('diagArea').innerHTML = statusParts.join(" | ");
                await loadDiagnostics(id);

                const sel = document.getElementById('sampleRate');
                sel.innerHTML = "";
                if (data.current_rate === null || data.current_rate === undefined) {
                    let opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    sel.add(opt);
                }
                data.supported_rates.forEach(r => {
                    let opt = document.createElement('option');
                    opt.value = r.enum_val; opt.text = r.str_val;
                    if(r.enum_val == data.current_rate) opt.selected = true;
                    sel.add(opt);
                });
                updateSampleRateHint();
                const runDataTypeSel = document.getElementById('samplingRunDataType');
                const currentRunDataType = String(data.current_data_type || 'float').toLowerCase();
                if (runDataTypeSel) {
                    runDataTypeSel.value = (currentRunDataType === 'calibrated') ? 'calibrated' : 'float';
                }

                const chArea = document.getElementById('channelsArea');
                chArea.innerHTML = "";
                chArea.innerHTML = `
                    <div class="channel-picker" id="channelPicker">
                        <button type="button" class="btn btn-outline-secondary channel-summary-btn" id="channelSummaryBtn" onclick="toggleChannelsMenu(event)">
                            0 active
                        </button>
                        <div class="channel-menu" id="channelMenu"></div>
                    </div>
                `;
                const channelNames = {
                    1: "Temperature (ch1)",
                    2: "CJC Temperature (ch2)"
                };
                const menu = document.getElementById('channelMenu');
                data.channels.forEach(ch => {
                    let checked = ch.enabled ? "checked" : "";
                    const label = channelNames[ch.id] || `Channel ${ch.id}`;
                    menu.innerHTML += `
                        <label class="channel-row">
                            <input class="form-check-input ch-enable" type="checkbox" id="ch_${ch.id}" ${checked} onchange="updateChannelSummary()">
                            <span>${label}</span>
                        </label>
                    `;
                });
                updateChannelSummary();
                document.getElementById('btnClearStorage').disabled = false;
                document.getElementById('btnExportStorage').disabled = false;
                document.getElementById('btnExportInflux').disabled = false;
                markReadState(true);
                markIdleState(String(data.state) === "0");
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = "Error: " + data.error;
                setOverviewValue('overviewLastRead', 'Read failed');
                setOverviewValue('overviewNodeState', 'Read failed');
                setOverviewValue('overviewLastComm', 'Unavailable');
                document.getElementById('btnClearStorage').disabled = true;
                document.getElementById('btnExportStorage').disabled = true;
                document.getElementById('btnExportInflux').disabled = true;
                markReadState(false);
                markIdleState(false);
            }
        } finally {
            if (wasIdle && idleConfirmed) btnRead.disabled = false;
        }
    }

    function parseLastCommAgeSec(lastCommStr) {
        if (!lastCommStr) return NaN;
        // Node time is treated as UTC from MSCL/WSDA, convert age from UTC.
        const iso = String(lastCommStr).replace(" ", "T") + "Z";
        const dt = new Date(iso);
        if (Number.isNaN(dt.getTime())) return NaN;
        return (Date.now() - dt.getTime()) / 1000;
    }

    function formatNodeLastCommLocal(lastCommStr) {
        if (!lastCommStr) return null;
        const dt = new Date(String(lastCommStr).replace(" ", "T") + "Z");
        if (Number.isNaN(dt.getTime())) return lastCommStr;
        const yyyy = dt.getFullYear();
        const mm = String(dt.getMonth() + 1).padStart(2, "0");
        const dd = String(dt.getDate()).padStart(2, "0");
        const hh = String(dt.getHours()).padStart(2, "0");
        const mi = String(dt.getMinutes()).padStart(2, "0");
        const ss = String(dt.getSeconds()).padStart(2, "0");
        return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
    }

    function formatStorageCapacity(capRaw) {
        const n = Number(capRaw);
        if (!Number.isFinite(n) || n < 0) return null;
        if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MiB (${n})`;
        if (n >= 1024) return `${(n / 1024).toFixed(2)} KiB (${n})`;
        return `${n}`;
    }

    async function probeNode() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnProbe');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "Probing node...";
        try {
            const res = await fetch(`api/probe/${id}`);
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `${data.message || 'Probe OK'}`;
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `${data.error || 'Probe failed'}`;
            }
            markIdleState(false);
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "Probe failed";
            markIdleState(false);
        } finally {
            btn.disabled = false;
        }
    }

    async function setNodeIdle() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnSetIdle');
        btn.disabled = true;
        try {
            statusDiv.className = "mt-2 text-center text-primary";
            statusDiv.innerHTML = "Sending Set to Idle...";
            const res = await fetch(`api/node_idle/${id}`, {method:'POST'});
            const data = await res.json();
            const st = data.idle_status || {};
            const idleResult = String(data.idle_result || st.idle_result || "pending");
            const sent = st.command_sent === true ? "sent" : "not-sent";
            const link = st.transport_alive === true ? "alive" : "no-link";
            const confirmed = st.state_confirmed === true ? "confirmed" : "pending";
            if (!data.success) {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `Idle result: ${idleResult} | ${data.error || 'Set to Idle failed'} | cmd:${sent} | link:${link} | idle:${confirmed}`;
                markIdleState(false);
            } else if (data.idle_confirmed) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `Idle result: ${idleResult} | Idle confirmed | cmd:${sent} | link:${link} | idle:${confirmed}`;
                markIdleState(true);
            } else {
                statusDiv.className = "mt-2 text-center status-warn";
                statusDiv.innerHTML = `Idle result: ${idleResult} (${data.reason || 'pending'}) | cmd:${sent} | link:${link} | idle:${confirmed}`;
                markIdleState(false);
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "Set to Idle failed";
            markIdleState(false);
        } finally {
            setTimeout(() => { btn.disabled = false; }, 1500);
        }
    }

    function toggleSamplingPanel() {
        const panel = document.getElementById('samplingPanel');
        panel.style.display = panel.style.display === 'none' ? '' : 'none';
        setSamplingActive(panel.style.display !== 'none');
        if (panel.style.display !== 'none') {
            samplingModeChanged();
            refreshSamplingRunStatus();
        }
    }

    function setSamplingActive(isActive) {
        document.body.classList.toggle('sampling-active', !!isActive);
    }

    function samplingModeChanged() {
        const when = document.getElementById('samplingRunWhen').value;
        const valueEl = document.getElementById('samplingRunDurationValue');
        const unitsEl = document.getElementById('samplingRunDurationUnits');
        const isContinuous = when === 'continuous';
        valueEl.disabled = isContinuous;
        unitsEl.disabled = isContinuous;
    }

    async function startSamplingRun() {
        const statusDiv = document.getElementById('samplingRunStatus');
        const btn = document.getElementById('btnSamplingStart');
        const rateRaw = document.getElementById('sampleRate').value;
        const when = document.getElementById('samplingRunWhen').value;
        const durationValueRaw = document.getElementById('samplingRunDurationValue').value;
        const durationUnits = document.getElementById('samplingRunDurationUnits').value;
        const mode = document.getElementById('samplingRunMode').value;
        const dataType = document.getElementById('samplingRunDataType').value || 'float';

        if (!rateRaw) {
            statusDiv.className = "small status-err mt-2";
            statusDiv.innerText = "Sample Rate is not available. Read node first.";
            return;
        }
        const body = {
            sample_rate: parseInt(rateRaw, 10),
            log_transmit_mode: mode,
            data_type: dataType,
            continuous: when === 'continuous',
            duration_value: when === 'continuous' ? 0 : (parseFloat(durationValueRaw || '0') || 0),
            duration_units: durationUnits
        };

        btn.disabled = true;
        try {
            statusDiv.className = "small text-primary mt-2";
            statusDiv.innerText = "Starting sampling for all configured nodes...";
            const res = await fetch(`api/sampling/group_start`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.success) {
                const run = data.run || {};
                const nodeIds = Array.isArray(run.node_ids) ? run.node_ids.join(", ") : "";
                statusDiv.className = "small status-ok mt-2";
                statusDiv.innerText = `RUNNING | nodes ${nodeIds} | ${run.mode_label || mode} | ${run.continuous ? 'continuous' : ((run.duration_sec || 0) + 's')}`;
                markIdleState(false);
            } else {
                statusDiv.className = "small status-err mt-2";
                statusDiv.innerText = `Start failed: ${data.error || 'unknown error'}`;
            }
        } catch (e) {
            statusDiv.className = "small status-err mt-2";
            statusDiv.innerText = "Start failed";
        } finally {
            btn.disabled = false;
            refreshSamplingRunStatus();
        }
    }

    async function stopSamplingRun() {
        const statusDiv = document.getElementById('samplingRunStatus');
        const btn = document.getElementById('btnSamplingStop');
        btn.disabled = true;
        try {
            statusDiv.className = "small text-primary mt-2";
            statusDiv.innerText = "Stopping sampling for all configured nodes...";
            const res = await fetch(`api/sampling/group_stop`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                const okNodes = Array.isArray(data.results) ? data.results.filter(x => x.success).map(x => x.node_id).join(", ") : "";
                statusDiv.className = "small status-ok mt-2";
                statusDiv.innerText = okNodes ? `Sampling stopped for nodes ${okNodes}` : (data.message || "Stop command sent");
                markIdleState(true);
                setSamplingActive(false);
            } else {
                statusDiv.className = "small status-err mt-2";
                statusDiv.innerText = `Stop failed: ${data.error || 'unknown error'}`;
            }
        } catch (e) {
            statusDiv.className = "small status-err mt-2";
            statusDiv.innerText = "Stop failed";
        } finally {
            btn.disabled = false;
            refreshSamplingRunStatus();
        }
    }

    async function refreshSamplingRunStatus() {
        const statusDiv = document.getElementById('samplingRunStatus');
        try {
            const res = await fetch(`api/sampling/group_status`);
            const data = await res.json();
            if (!data.success) return;
            const items = Array.isArray(data.items) ? data.items : [];
            const runningItems = items.filter(x => (x.run || {}).state === "running");
            const run = runningItems[0] ? (runningItems[0].run || {}) : {};
            const mode = run.mode_label || "-";
            const leftVals = runningItems.map(x => {
                const itemRun = x.run || {};
                const duration = parseInt(itemRun.duration_sec || 0, 10);
                const started = parseInt(itemRun.started_at || 0, 10);
                if (!(duration > 0 && started > 0)) return null;
                return Math.max(0, duration - Math.max(0, Math.floor(Date.now() / 1000) - started));
            }).filter(v => v !== null);
            const left = leftVals.length ? ` | left ${Math.min(...leftVals)}s` : "";
            const summary = items.map(x => `${x.node_id}:${x.node_state || "Unknown"}`).join(" | ");
            statusDiv.className = "small text-muted mt-2";
            statusDiv.innerText = `State ${data.any_running ? "running" : "idle"} | mode ${mode}${left} | ${summary}`;
            const isRunning = !!data.any_running;
            setSamplingActive(isRunning);
        } catch (e) {
            // silent
        }
    }

    async function setNodeSleep() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnSleep');
        const confirmed = window.confirm(
            "Put node into SLEEP mode?\n\nWake requires physical power cycle."
        );
        if (!confirmed) return;
        btn.disabled = true;
        try {
            statusDiv.className = "mt-2 text-center text-primary";
            statusDiv.innerHTML = "Sending Sleep command...";
            const res = await fetch(`api/node_sleep/${id}`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `${data.message}`;
                markIdleState(false);
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `${data.error || 'Sleep failed'}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "Sleep failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function cycleNodePower() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnCyclePower');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "Sending Power Cycle...";
        try {
            const res = await fetch(`api/node_cycle_power/${id}`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "Power cycle command sent";
                markIdleState(false);
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `${data.error}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "Power cycle failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function clearStorage() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnClearStorage');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "Clearing storage...";
        try {
            const res = await fetch(`api/clear_storage/${id}`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "Storage cleared";
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `${data.error || 'Clear storage failed'}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "Clear storage failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function exportStorageCsv() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnExportStorage');
        btn.disabled = true;
        const uiHoursInput = prompt(
            "Export last N hours (browser time).\nExamples: 1, 6, 24.\nLeave empty for full storage export.",
            "24"
        );
        if (uiHoursInput === null) {
            btn.disabled = false;
            return;
        }
        let timeWindowParams = "";
        const uiHoursTxt = String(uiHoursInput || "").trim();
        if (uiHoursTxt !== "") {
            const h = Number(uiHoursTxt);
            if (!Number.isFinite(h) || h <= 0) {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = "Invalid hours value";
                btn.disabled = false;
                return;
            }
            const toIso = new Date().toISOString();
            const fromIso = new Date(Date.now() - (h * 3600 * 1000)).toISOString();
            timeWindowParams = `&ui_from=${encodeURIComponent(fromIso)}&ui_to=${encodeURIComponent(toIso)}`;
        }
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "Exporting CSV from node storage...";
        try {
            const res = await fetch(`api/export_storage/${id}?format=csv&ingest_influx=0${timeWindowParams}`);
            if (!res.ok) {
                let err = "Export failed";
                try {
                    const data = await res.json();
                    err = data.error || err;
                } catch (e) { }
                throw new Error(err);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const cd = res.headers.get('content-disposition') || '';
            let filename = `node_${id}_datalog.csv`;
            const match = cd.match(/filename=([^;]+)/i);
            if (match && match[1]) {
                filename = match[1].replace(/\"/g, '').trim();
            }
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            statusDiv.className = "mt-2 text-center status-ok";
            statusDiv.innerHTML = "CSV downloaded";
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = `${e.message || 'Export failed'}`;
        } finally {
            btn.disabled = false;
        }
    }

    async function exportStorageToInflux() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnExportInflux');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "Exporting node storage to Influx node-export stream...";
        try {
            const res = await fetch(`api/export_storage/${id}?format=none&ingest_influx=1`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Export to Influx failed");
            }
            const written = parseInt(String(data.backfill_written || 0), 10);
            const skipped = parseInt(String(data.backfill_skipped_existing || 0), 10);
            statusDiv.className = "mt-2 text-center status-ok";
            statusDiv.innerHTML = `Exported to Influx: ${written} new | ${skipped} already existed`;
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = `${e.message || 'Export to Influx failed'}`;
        } finally {
            btn.disabled = false;
        }
    }

    async function applyConfig() {
        const id = document.getElementById('nodeId').value;
        const rate = document.getElementById('sampleRate').value;
        const power = document.getElementById('txPower').value;
        const inputRangeRaw = document.getElementById('inputRange').value;
        const unitRaw = document.getElementById('calibrationUnit').value;
        const cjcUnitRaw = document.getElementById('cjcCalibrationUnit').value;
        const lowPassFilterRaw = document.getElementById('lowPassFilter').value;
        const storageLimitModeRaw = document.getElementById('storageLimitMode').value;
        const lostBeaconTimeoutRaw = document.getElementById('lostBeaconTimeout').value;
        const diagnosticIntervalRaw = document.getElementById('diagnosticInterval').value;
        const lostBeaconEnabled = document.getElementById('lostBeaconEnabled').checked;
        const diagnosticEnabled = document.getElementById('diagnosticEnabled').checked;
        const defaultModeRaw = document.getElementById('defaultMode').value;
        const inactivityTimeoutRaw = document.getElementById('inactivityTimeout').value;
        const inactivityEnabled = document.getElementById('inactivityEnabled').checked;
        const checkRadioIntervalRaw = document.getElementById('checkRadioInterval').value;
        const dataModeRaw = document.getElementById('dataMode').value;
        const transducerTypeRaw = document.getElementById('transducerType').value;
        const sensorTypeRaw = document.getElementById('sensorType').value;
        const wireTypeRaw = document.getElementById('wireType').value;
        const statusDiv = document.getElementById('writeStatus');
        if (!idleConfirmed) {
            statusDiv.className = "mt-3 text-center status-err";
            statusDiv.innerHTML = "Node is not confirmed in Idle. Press Set to Idle first.";
            return;
        }
        if (!rate || Number.isNaN(parseInt(rate))) {
            statusDiv.className = "mt-3 text-center status-err";
            statusDiv.innerHTML = "Sample Rate is unknown (N/A). Run FULL READ once or configure in SensorConnect.";
            return;
        }
        if (transducerTypeRaw !== "" && sensorTypeRaw === "") {
            statusDiv.className = "mt-3 text-center status-err";
            statusDiv.innerHTML = "Sensor Type is required for selected Transducer Type.";
            return;
        }
        if (transducerTypeRaw === "1" && wireTypeRaw === "") {
            statusDiv.className = "mt-3 text-center status-err";
            statusDiv.innerHTML = "Wire Type is required for RTD.";
            return;
        }

        let activeChs = [];
        document.querySelectorAll('.ch-enable').forEach(cb => {
            if(cb.checked) { activeChs.push(parseInt(cb.id.replace('ch_', ''))); }
        });
        
        statusDiv.className = "mt-3 text-center text-primary";
        statusDiv.innerHTML = "Writing config...";
        try {
            const payload = {
                node_id: id,
                sample_rate: parseInt(rate),
                tx_power: parseInt(power),
                channels: activeChs
            };
            if (writeCaps.supportsInputRange) payload.input_range = inputRangeRaw === "" ? null : parseInt(inputRangeRaw);
            if (writeCaps.supportsCalibrationUnit) payload.unit = unitRaw === "" ? null : parseInt(unitRaw);
            if (writeCaps.supportsCjcUnit) payload.cjc_unit = cjcUnitRaw === "" ? null : parseInt(cjcUnitRaw);
            if (writeCaps.supportsLowPassFilter) payload.low_pass_filter = lowPassFilterRaw === "" ? null : parseInt(lowPassFilterRaw);
            if (writeCaps.supportsStorageLimitMode) payload.storage_limit_mode = storageLimitModeRaw === "" ? null : parseInt(storageLimitModeRaw);
            payload.lost_beacon_timeout = lostBeaconTimeoutRaw === "" ? null : parseInt(lostBeaconTimeoutRaw);
            payload.diagnostic_interval = diagnosticIntervalRaw === "" ? null : parseInt(diagnosticIntervalRaw);
            payload.lost_beacon_enabled = !!lostBeaconEnabled;
            payload.diagnostic_enabled = !!diagnosticEnabled;
            if (writeCaps.supportsDefaultMode) payload.default_mode = defaultModeRaw === "" ? null : parseInt(defaultModeRaw);
            if (writeCaps.supportsInactivityTimeout) {
                payload.inactivity_timeout = inactivityTimeoutRaw === "" ? null : parseInt(inactivityTimeoutRaw);
                payload.inactivity_enabled = !!inactivityEnabled;
            }
            if (writeCaps.supportsCheckRadioInterval) payload.check_radio_interval = checkRadioIntervalRaw === "" ? null : parseInt(checkRadioIntervalRaw);
            if (writeCaps.supportsDataMode) payload.data_mode = dataModeRaw === "" ? null : parseInt(dataModeRaw);
            if (writeCaps.supportsTransducerType) payload.transducer_type = transducerTypeRaw === "" ? null : parseInt(transducerTypeRaw);
            if (writeCaps.supportsTempSensorOptions) {
                payload.sensor_type = sensorTypeRaw === "" ? null : parseInt(sensorTypeRaw);
                payload.wire_type = wireTypeRaw === "" ? null : parseInt(wireTypeRaw);
            }

            const res = await fetch('api/write', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            statusDiv.innerHTML = data.success ? "<span class='status-ok'>SAVED SUCCESSFULLY</span>" : `<span class='status-err'>${data.error}</span>`;
        } catch (e) { }
    }

    function updateChannelSummary() {
        const all = document.querySelectorAll('.ch-enable');
        let active = 0;
        all.forEach(cb => { if (cb.checked) active += 1; });
        const btn = document.getElementById('channelSummaryBtn');
        if (btn) btn.innerText = `${active} active`;
    }

    function toggleChannelsMenu(event) {
        event.preventDefault();
        event.stopPropagation();
        const menu = document.getElementById('channelMenu');
        if (!menu) return;
        menu.classList.toggle('open');
    }

    document.addEventListener('click', function(evt) {
        const picker = document.getElementById('channelPicker');
        const menu = document.getElementById('channelMenu');
        if (!picker || !menu) return;
        if (!picker.contains(evt.target)) {
            menu.classList.remove('open');
        }
    });

    document.getElementById('nodeId').addEventListener('input', function() {
        document.getElementById('configCard').style.display = "none";
        const statusDiv = document.getElementById('readStatus');
        if (statusDiv) {
            statusDiv.className = "info-surface-value fw-bold small";
            statusDiv.innerHTML = "";
        }
        const btn = document.getElementById('btnClearStorage');
        if (btn) btn.disabled = true;
        const exportBtn = document.getElementById('btnExportStorage');
        if (exportBtn) exportBtn.disabled = true;
        const exportInfluxBtn = document.getElementById('btnExportInflux');
        if (exportInfluxBtn) exportInfluxBtn.disabled = true;
        const metaDiv = document.getElementById('readMeta');
        if (metaDiv) metaDiv.innerText = "";
        markReadState(false);
        resetNodeOverview();
    });
    document.getElementById('lostBeaconEnabled').addEventListener('change', function() {
        document.getElementById('lostBeaconTimeout').disabled = !this.checked;
    });
    document.getElementById('diagnosticEnabled').addEventListener('change', function() {
        document.getElementById('diagnosticInterval').disabled = !this.checked;
    });
    document.getElementById('inactivityEnabled').addEventListener('change', function() {
        document.getElementById('inactivityTimeout').disabled = !this.checked;
    });

    async function refreshBaseStatus() {
        try {
            const res = await fetch('api/status');
            const data = await res.json();
            const text = document.getElementById('baseStatusText');
            const pill = document.getElementById('baseStatusPill');
            const heroBaseModel = document.getElementById('heroBaseModel');
            const heroBaseSerial = document.getElementById('heroBaseSerial');
            const heroLinkHealth = document.getElementById('heroLinkHealth');
            const heroPort = document.getElementById('heroPort');
            if (data.connected) {
                pill.className = "status-pill status-ok-pill mb-3";
                pill.innerText = "Connected";
            } else {
                pill.className = "status-pill status-err-pill mb-3";
                pill.innerText = "Disconnected";
            }
            if (data.link_health === "healthy") {
                pill.className = "status-pill status-ok-pill mb-3";
                pill.innerText = "Link: Healthy";
            } else if (data.link_health === "degraded") {
                pill.className = "status-pill status-warn-pill mb-3";
                pill.innerText = "Link: Degraded";
            } else if (data.link_health === "offline") {
                pill.className = "status-pill status-err-pill mb-3";
                pill.innerText = "Link: Offline";
            }
            if (heroBaseModel) heroBaseModel.innerText = data.base_model || "Not detected";
            if (heroBaseSerial) heroBaseSerial.innerText = data.base_serial || "Unavailable";
            if (heroLinkHealth) heroLinkHealth.innerText = data.link_health || "offline";
            if (heroPort) heroPort.innerText = data.port || "unset";
            window.currentBeaconState = data.beacon_state;
            const beaconBtn = document.getElementById('btnBeacon');
            if (data.beacon_state === true) {
                beaconBtn.innerText = "Beacon: ON";
            } else if (data.beacon_state === false) {
                beaconBtn.innerText = "Beacon: OFF";
            } else {
                beaconBtn.innerText = "Beacon: ?";
            }
            const line1 = [
                `Status: ${data.message}`,
                `Port: ${data.port}`,
                `Last: ${data.ts || 'N/A'}`,
                data.base_model ? `Model: ${data.base_model}` : null,
                data.base_fw ? `FW: ${data.base_fw}` : null,
                data.base_serial ? `S/N: ${data.base_serial}` : null,
                data.base_connection ? `Connection: ${data.base_connection}` : null,
                data.base_region ? `Region: ${data.base_region}` : null,
                data.base_radio ? `Radio: ${data.base_radio}` : null
            ].filter(Boolean).join(" | ");
            const line2 = [
                data.base_last_comm ? `Last Comm: ${data.base_last_comm}` : null,
                data.base_link ? `State: ${data.base_link}` : null,
                data.link_health ? `Link Health: ${data.link_health}` : null,
                data.link_health_reason ? `Reason: ${data.link_health_reason}` : null,
                (data.ping_age_sec !== null && data.ping_age_sec !== undefined) ? `Ping Age: ${data.ping_age_sec}s` : null,
                (data.comm_age_sec !== null && data.comm_age_sec !== undefined) ? `Comm Age: ${data.comm_age_sec}s` : null
            ].filter(Boolean).join(" | ");
            text.innerText = line2 ? `${line1}\n${line2}` : line1;
        } catch (e) { }
    }

    async function toggleBeacon() {
        try {
            const current = window.currentBeaconState;
            const target = (current === true) ? false : true;
            const res = await fetch('api/beacon', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: target})
            });
            const data = await res.json();
            const statusDiv = document.getElementById('readStatus');
            if (!data.success) {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `${data.error}`;
            } else {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `${data.message}`;
            }
            await refreshBaseStatus();
        } catch (e) { }
    }

    async function loadDiagnostics(id) {
        try {
            const res = await fetch(`api/diagnostics/${id}`);
            const data = await res.json();
            const target = document.getElementById('diagFlags');
            if (!data.success) {
                target.innerText = "Diagnostics: " + data.error;
                return;
            }
            const parts = data.flags.map(f => {
                if (typeof f.value === 'boolean') {
                    return `${f.name}: <span class='fw-bold'>${f.value ? 'YES' : 'NO'}</span>`;
                }
                return `${f.name}: <span class='fw-bold'>${f.value ?? 'N/A'}</span>`;
            });
            parts.push("<span class='fst-italic'>Feature flags may be conservative for this firmware/MSCL build.</span>");
            target.innerHTML = parts.join(" | ");
        } catch (e) { }
    }

    async function refreshLogs() {
        try {
            const res = await fetch('api/logs');
            const data = await res.json();
            const box = document.getElementById('logBox');
            const joined = (data.logs || []).join('\n');
            box.textContent = joined.replace(/\\n/g, '\n');
        } catch (e) { }
    }

    async function copyLogs() {
        const box = document.getElementById('logBox');
        const statusDiv = document.getElementById('readStatus');
        const text = box ? box.textContent || "" : "";
        try {
            await navigator.clipboard.writeText(text);
            statusDiv.className = "mt-2 text-center status-ok";
            statusDiv.innerHTML = "Logs copied";
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "Copy failed";
        }
    }

    // Manual refresh only (no auto status refresh)
    updateReadWriteButtons();
    updateSampleRateHint();
    resetNodeOverview();
    refreshBaseStatus();
    refreshLogs();
