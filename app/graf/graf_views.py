from typing import Any

from flask import render_template


_PANEL_ORDER: list[tuple[str, str]] = [
    ("show_redlab", "c1"),
    ("show_mscl", "c2"),
    ("show_matter", "c3"),
    ("show_almemo", "c4"),
    ("show_pyrometers", "c5"),
    ("show_messkluppe", "c6"),
    ("show_messkluppe_orientation", "c7"),
    ("show_messkluppe_battery", "c8"),
    ("show_messkluppe_temperatures", "c9"),
    ("show_matter_battery", "c10"),
    ("show_matter_humidity", "c11"),
    ("show_matter_pressure", "c12"),
    ("show_matter_pm", "c13"),
]

VIEW_CONFIGS: dict[str, dict[str, Any]] = {
    "all": {
        "title": "Graf App Lite",
        "show_redlab": True,
        "show_mscl": True,
        "show_matter": True,
        "show_matter_humidity": True,
        "show_matter_pressure": True,
        "show_matter_pm": True,
        "show_almemo": True,
        "show_pyrometers": True,
        "show_messkluppe": True,
        "show_messkluppe_orientation": True,
        "show_messkluppe_battery": True,
        "show_messkluppe_temperatures": True,
        "show_matter_battery": True,
    },
    "redlab": {
        "title": "Graf App Lite · RedLab",
        "show_redlab": True,
        "show_mscl": False,
        "show_almemo": False,
        "show_pyrometers": False,
        "show_messkluppe": False,
        "device": {
            "panel_title": "Measurement Computing Corp. USB-TC",
            "chart_id": "c1",
            "list_id": "tempChannelList",
            "toggle_all_id": "tempChannelToggleAll",
            "list_kind": "redlab",
            "export_kind": "redlab",
        },
    },
    "mscl": {
        "title": "Graf App Lite · MSCL",
        "show_redlab": False,
        "show_mscl": True,
        "show_almemo": False,
        "show_pyrometers": False,
        "show_messkluppe": False,
        "device": {
            "panel_title": "MSCL Temperature",
            "chart_id": "c2",
            "list_id": "seriesChannelList-c2",
            "toggle_all_id": "seriesChannelToggleAll-c2",
            "list_kind": "series",
            "export_kind": "mscl",
        },
    },
    "almemo": {
        "title": "Graf App Lite · ALMEMO",
        "show_redlab": False,
        "show_mscl": False,
        "show_matter": False,
        "show_almemo": True,
        "show_pyrometers": False,
        "show_messkluppe": False,
        "device": {
            "panel_title": "ALMEMO 2490",
            "chart_id": "c4",
            "list_id": "seriesChannelList-c4",
            "toggle_all_id": "seriesChannelToggleAll-c4",
            "list_kind": "series",
            "export_kind": "almemo",
        },
    },
    "pyrometers": {
        "title": "Graf App Lite · Pyrometers",
        "show_redlab": False,
        "show_mscl": False,
        "show_matter": False,
        "show_almemo": False,
        "show_pyrometers": True,
        "show_messkluppe": False,
        "device": {
            "panel_title": "Pyrometers",
            "chart_id": "c5",
            "list_id": "seriesChannelList-c5",
            "toggle_all_id": "seriesChannelToggleAll-c5",
            "list_kind": "series",
            "export_kind": "pyrometers",
        },
    },
    "matter": {
        "title": "Graf App Lite · Matter",
        "show_redlab": False,
        "show_mscl": False,
        "show_matter": True,
        "show_matter_humidity": True,
        "show_matter_pressure": True,
        "show_matter_pm": True,
        "show_matter_battery": True,
        "show_almemo": False,
        "show_pyrometers": False,
        "show_messkluppe": False,
    },
    "messkluppe": {
        "title": "Graf App Lite · Messkluppe",
        "show_redlab": False,
        "show_mscl": False,
        "show_matter": False,
        "show_almemo": False,
        "show_pyrometers": False,
        "show_messkluppe": True,
        "show_messkluppe_orientation": True,
        "show_messkluppe_battery": True,
        "show_messkluppe_temperatures": True,
    },
}


def get_view_config(view_name: str) -> dict[str, Any]:
    return dict(VIEW_CONFIGS.get(view_name, VIEW_CONFIGS["all"]))


def _export_modals(view_name: str, cfg: dict) -> list[dict[str, str]]:
    modals = []
    if view_name == "all":
        modals.append({"key": "Temps", "title": "Export All CSV"})
    if cfg.get("show_mscl"):
        modals.append({"key": "Mscl", "title": "Export MSCL CSV"})
    if cfg.get("show_redlab") and view_name == "redlab":
        modals.append({"key": "Redlab", "title": "Export RedLab CSV"})
    if cfg.get("show_almemo") and view_name == "almemo":
        modals.append({"key": "Almemo", "title": "Export ALMEMO CSV"})
    if cfg.get("show_pyrometers") and view_name == "pyrometers":
        modals.append({"key": "Pyrometers", "title": "Export Pyrometers CSV"})
    if cfg.get("show_matter") and view_name == "matter":
        modals.append({"key": "Matter", "title": "Export Matter CSV"})
    if cfg.get("show_messkluppe") and view_name == "messkluppe":
        modals.append({"key": "Messkluppe", "title": "Export Messkluppe CSV"})
    return modals


def render_index(
    view_name: str,
    default_range: str,
    default_refresh_sec: int,
    allowed_ranges: dict[str, int],
):
    cfg = get_view_config(view_name)
    return render_template(
        "index.html",
        page_mode=view_name,
        page_title=str(cfg["title"]),
        show_redlab=bool(cfg["show_redlab"]),
        show_mscl=bool(cfg["show_mscl"]),
        show_matter=bool(cfg.get("show_matter", False)),
        show_matter_humidity=bool(cfg.get("show_matter_humidity", False)),
        show_matter_pressure=bool(cfg.get("show_matter_pressure", False)),
        show_matter_pm=bool(cfg.get("show_matter_pm", False)),
        show_matter_battery=bool(cfg.get("show_matter_battery", False)),
        show_almemo=bool(cfg.get("show_almemo", False)),
        show_pyrometers=bool(cfg.get("show_pyrometers", False)),
        show_messkluppe=bool(cfg.get("show_messkluppe", False)),
        show_messkluppe_orientation=bool(cfg.get("show_messkluppe_orientation", False)),
        show_messkluppe_battery=bool(cfg.get("show_messkluppe_battery", False)),
        show_messkluppe_temperatures=bool(cfg.get("show_messkluppe_temperatures", False)),
        chart_ids=[cid for flag, cid in _PANEL_ORDER if cfg.get(flag)],
        export_modals=_export_modals(view_name, cfg),
        single_device_mode=view_name in {"redlab", "mscl", "almemo", "pyrometers"},
        device=cfg.get("device"),
        default_range=default_range,
        default_refresh_sec=default_refresh_sec,
        ranges=sorted(allowed_ranges.keys(), key=lambda key: allowed_ranges[key]),
    )
