const EXPORT_CONFIGS = {
  temps: {
    key: "temps",
    route: "api/export/all.csv",
    modeName: "exportTempsMode",
    modalId: "exportTempsModal",
    rateWrapId: "exportTempsRateWrap",
    presetId: "exportHzPresetTemps",
    customInputId: "exportHzCustomTemps",
    delimiterId: "exportDelimiterTemps",
    openButtonIds: ["btnExportAll", "btnExportTemps"],
    confirmButtonId: "btnExportTempsConfirm",
    cancelButtonId: "btnExportTempsCancel",
    errorLabel: "export rate",
  },
  mscl: {
    key: "mscl",
    route: "api/export/mscl.csv",
    modeName: "exportMsclMode",
    modalId: "exportMsclModal",
    rateWrapId: "exportMsclRateWrap",
    presetId: "exportHzPresetMscl",
    customInputId: "exportHzCustomMscl",
    delimiterId: "exportDelimiterMscl",
    openButtonIds: ["btnExportMscl"],
    confirmButtonId: "btnExportMsclConfirm",
    cancelButtonId: "btnExportMsclCancel",
    errorLabel: "MSCL export rate",
  },
  redlab: {
    key: "redlab",
    route: "api/export/redlab.csv",
    modeName: "exportRedlabMode",
    modalId: "exportRedlabModal",
    rateWrapId: "exportRedlabRateWrap",
    presetId: "exportHzPresetRedlab",
    customInputId: "exportHzCustomRedlab",
    delimiterId: "exportDelimiterRedlab",
    openButtonIds: ["btnExportRedlab"],
    confirmButtonId: "btnExportRedlabConfirm",
    cancelButtonId: "btnExportRedlabCancel",
    errorLabel: "RedLab export rate",
  },
  almemo: {
    key: "almemo",
    route: "api/export/almemo.csv",
    modeName: "exportAlmemoMode",
    modalId: "exportAlmemoModal",
    rateWrapId: "exportAlmemoRateWrap",
    presetId: "exportHzPresetAlmemo",
    customInputId: "exportHzCustomAlmemo",
    delimiterId: "exportDelimiterAlmemo",
    openButtonIds: ["btnExportAlmemo"],
    confirmButtonId: "btnExportAlmemoConfirm",
    cancelButtonId: "btnExportAlmemoCancel",
    errorLabel: "ALMEMO export rate",
  },
  pyrometers: {
    key: "pyrometers",
    route: "api/export/pyrometers.csv",
    modeName: "exportPyrometersMode",
    modalId: "exportPyrometersModal",
    rateWrapId: "exportPyrometersRateWrap",
    presetId: "exportHzPresetPyrometers",
    customInputId: "exportHzCustomPyrometers",
    delimiterId: "exportDelimiterPyrometers",
    openButtonIds: ["btnExportPyrometers"],
    confirmButtonId: "btnExportPyrometersConfirm",
    cancelButtonId: "btnExportPyrometersCancel",
    errorLabel: "pyrometers export rate",
  },
  matter: {
    key: "matter",
    route: "api/export/matter.csv",
    modeName: "exportMatterMode",
    modalId: "exportMatterModal",
    rateWrapId: "exportMatterRateWrap",
    presetId: "exportHzPresetMatter",
    customInputId: "exportHzCustomMatter",
    delimiterId: "exportDelimiterMatter",
    openButtonIds: ["btnExportMatter"],
    confirmButtonId: "btnExportMatterConfirm",
    cancelButtonId: "btnExportMatterCancel",
    errorLabel: "matter export rate",
  },
  messkluppe: {
    key: "messkluppe",
    route: "api/export/messkluppe.csv",
    modeName: "exportMesskluppeMode",
    modalId: "exportMesskluppeModal",
    rateWrapId: "exportMesskluppeRateWrap",
    presetId: "exportHzPresetMesskluppe",
    customInputId: "exportHzCustomMesskluppe",
    delimiterId: "exportDelimiterMesskluppe",
    openButtonIds: ["btnExportMesskluppe"],
    confirmButtonId: "btnExportMesskluppeConfirm",
    cancelButtonId: "btnExportMesskluppeCancel",
    errorLabel: "Messkluppe export rate",
  },
};

function getExportConfig(key) {
  return EXPORT_CONFIGS[key] || null;
}

function getExportMode(config) {
  const selected = document.querySelector(`input[name="${config.modeName}"]:checked`);
  return selected ? String(selected.value || "view") : "view";
}

function updateExportModalUi(config) {
  const mode = getExportMode(config);
  const rateWrap = document.getElementById(config.rateWrapId);
  const preset = document.getElementById(config.presetId);
  const customInp = document.getElementById(config.customInputId);
  const showRate = mode === "custom_rate";
  if (rateWrap) rateWrap.style.display = showRate ? "grid" : "none";
  if (customInp && preset) customInp.style.display = (showRate && preset.value === "custom") ? "" : "none";
}

function closeExportModal(config) {
  const modal = document.getElementById(config.modalId);
  if (!modal) return;
  modal.style.display = "none";
  modal.setAttribute("aria-hidden", "true");
}

function openExportModal(config) {
  const modal = document.getElementById(config.modalId);
  if (!modal) return;
  modal.style.display = "flex";
  modal.setAttribute("aria-hidden", "false");
}

function exportCsv(config) {
  const params = buildDashboardParams();
  if (!params) return;
  const mode = getExportMode(config);
  params.set("export_mode", mode);
  const delimiter = document.getElementById(config.delimiterId);
  if (delimiter) params.set("csv_delimiter", String(delimiter.value || "comma"));
  if (mode === "custom_rate") {
    const preset = document.getElementById(config.presetId);
    const customInp = document.getElementById(config.customInputId);
    let hz = NaN;
    if (preset && preset.value !== "custom") {
      hz = Number(preset.value);
    } else if (customInp) {
      hz = Number(customInp.value);
    }
    if (!Number.isFinite(hz) || hz <= 0) {
      setStatus(`error: set valid ${config.errorLabel} in Hz`);
      return;
    }
    params.set("export_hz", String(hz));
  }
  window.location.href = `${config.route}?${params.toString()}`;
  closeExportModal(config);
}

function refreshExportModalUiAll() {
  Object.values(EXPORT_CONFIGS).forEach((config) => updateExportModalUi(config));
}

function closeAllExportModals() {
  Object.values(EXPORT_CONFIGS).forEach((config) => closeExportModal(config));
}

function initExportModals() {
  Object.values(EXPORT_CONFIGS).forEach((config) => {
    config.openButtonIds.forEach((buttonId) => {
      const btn = document.getElementById(buttonId);
      if (btn) btn.addEventListener("click", () => openExportModal(config));
    });

    const confirmBtn = document.getElementById(config.confirmButtonId);
    if (confirmBtn) confirmBtn.addEventListener("click", () => exportCsv(config));

    const cancelBtn = document.getElementById(config.cancelButtonId);
    if (cancelBtn) cancelBtn.addEventListener("click", () => closeExportModal(config));

    document.querySelectorAll(`input[name="${config.modeName}"]`).forEach((el) => {
      el.addEventListener("change", () => updateExportModalUi(config));
    });

    const preset = document.getElementById(config.presetId);
    if (preset) preset.addEventListener("change", () => updateExportModalUi(config));

    const modal = document.getElementById(config.modalId);
    if (modal) {
      modal.addEventListener("click", (e) => {
        if (e.target && e.target.id === config.modalId) closeExportModal(config);
      });
    }
  });
}
