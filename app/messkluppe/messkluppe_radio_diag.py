from __future__ import annotations

import os
import time
from typing import Any


R_REGISTER = 0x00
W_REGISTER = 0x20
REGISTER_MASK = 0x1F
NOP = 0xFF

CONFIG = 0x00
EN_AA = 0x01
EN_RXADDR = 0x02
SETUP_AW = 0x03
SETUP_RETR = 0x04
RF_CH = 0x05
RF_SETUP = 0x06
STATUS = 0x07
RX_ADDR_P0 = 0x0A
RX_ADDR_P1 = 0x0B
RX_PW_P0 = 0x11
RX_PW_P1 = 0x12
FIFO_STATUS = 0x17
DYNPD = 0x1C
FEATURE = 0x1D


def env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def radio_diag_config_from_env() -> dict[str, Any]:
    return {
        "spi_bus": env_int("MESSKLUPPE_RADIO_SPI_BUS", 0, minimum=0, maximum=10),
        "spi_device": env_int("MESSKLUPPE_RADIO_SPI_DEVICE", 0, minimum=0, maximum=10),
        "ce_gpio": env_int("MESSKLUPPE_RADIO_CE_GPIO", 25, minimum=0, maximum=255),
        "channel": env_int("MESSKLUPPE_RADIO_CHANNEL", 111, minimum=0, maximum=125),
        "payload_size": env_int("MESSKLUPPE_RADIO_PAYLOAD_SIZE", 32, minimum=1, maximum=32),
        "spi_speed_hz": env_int("MESSKLUPPE_RADIO_SPI_SPEED_HZ", 4_000_000, minimum=100_000, maximum=10_000_000),
    }


def _hex(value: int) -> str:
    return f"0x{int(value) & 0xFF:02x}"


def _read_register(spi: Any, reg: int, length: int = 1) -> int | list[int]:
    resp = spi.xfer2([R_REGISTER | (REGISTER_MASK & reg), *([NOP] * length)])
    values = resp[1 : length + 1]
    return values[0] if length == 1 else values


def _write_register(spi: Any, reg: int, value: int | list[int]) -> int:
    values = value if isinstance(value, list) else [int(value) & 0xFF]
    resp = spi.xfer2([W_REGISTER | (REGISTER_MASK & reg), *values])
    return int(resp[0])


def _looks_connected(registers: dict[str, Any]) -> bool:
    # Floating or disconnected MISO commonly reads all 0x00 or all 0xff.
    values = [
        int(registers.get("status", 0)),
        int(registers.get("rf_ch", 0)),
        int(registers.get("rf_setup", 0)),
        int(registers.get("config", 0)),
        int(registers.get("setup_aw", 0)),
    ]
    if all(value == 0x00 for value in values) or all(value == 0xFF for value in values):
        return False
    setup_aw = int(registers.get("setup_aw", 0))
    rf_ch = int(registers.get("rf_ch", 255))
    return setup_aw in {1, 2, 3} and 0 <= rf_ch <= 125


def _claim_ce_gpio(ce_gpio: int) -> tuple[dict[str, Any], Any]:
    try:
        import lgpio  # type: ignore

        chip = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(chip, ce_gpio, 0)
        lgpio.gpio_write(chip, ce_gpio, 0)
        return {"ok": True, "gpio": ce_gpio, "backend": "lgpio", "chip": 0}, ("lgpio", lgpio, chip, ce_gpio)
    except Exception as lgpio_exc:
        try:
            import RPi.GPIO as gpio_module  # type: ignore

            gpio_module.setmode(gpio_module.BCM)
            gpio_module.setwarnings(False)
            gpio_module.setup(ce_gpio, gpio_module.OUT)
            gpio_module.output(ce_gpio, gpio_module.LOW)
            return {
                "ok": True,
                "gpio": ce_gpio,
                "backend": "RPi.GPIO",
                "lgpio_error": str(lgpio_exc),
            }, ("rpi_gpio", gpio_module, ce_gpio)
        except Exception as rpi_exc:
            return {
                "ok": False,
                "gpio": ce_gpio,
                "backend": "unavailable",
                "lgpio_error": str(lgpio_exc),
                "rpi_gpio_error": str(rpi_exc),
            }, None


def _release_ce_gpio(handle: Any) -> None:
    if not handle:
        return
    backend, module, *tokens = handle
    if backend == "lgpio":
        chip, ce_gpio = tokens
        try:
            module.gpio_write(chip, ce_gpio, 0)
        except Exception:
            pass
        try:
            module.gpio_free(chip, ce_gpio)
        except Exception:
            pass
        try:
            module.gpiochip_close(chip)
        except Exception:
            pass
    elif backend == "rpi_gpio":
        ce_gpio = tokens[0]
        try:
            module.cleanup(ce_gpio)
        except Exception:
            pass


def run_radio_diagnostics() -> dict[str, Any]:
    cfg = radio_diag_config_from_env()
    started = time.time()
    result: dict[str, Any] = {
        "ok": False,
        "configured": cfg,
        "checks": {},
        "registers": {},
        "details": {},
        "duration_ms": None,
        "error": "",
    }

    try:
        import spidev  # type: ignore
    except Exception as exc:
        result["error"] = f"spidev_import_error: {exc}"
        result["duration_ms"] = round((time.time() - started) * 1000.0, 1)
        return result

    ce_handle = None
    result["checks"]["ce_gpio"], ce_handle = _claim_ce_gpio(cfg["ce_gpio"])

    spi = spidev.SpiDev()
    try:
        spi.open(cfg["spi_bus"], cfg["spi_device"])
        spi.max_speed_hz = cfg["spi_speed_hz"]
        result["checks"]["spi_open"] = {
            "ok": True,
            "device": f"/dev/spidev{cfg['spi_bus']}.{cfg['spi_device']}",
            "max_speed_hz": cfg["spi_speed_hz"],
        }

        _write_register(spi, RF_CH, cfg["channel"])
        _write_register(spi, RX_PW_P1, cfg["payload_size"])
        registers = {
            "status": int(spi.xfer2([NOP])[0]),
            "config": int(_read_register(spi, CONFIG)),
            "en_aa": int(_read_register(spi, EN_AA)),
            "en_rxaddr": int(_read_register(spi, EN_RXADDR)),
            "setup_aw": int(_read_register(spi, SETUP_AW)),
            "setup_retr": int(_read_register(spi, SETUP_RETR)),
            "rf_ch": int(_read_register(spi, RF_CH)),
            "rf_setup": int(_read_register(spi, RF_SETUP)),
            "fifo_status": int(_read_register(spi, FIFO_STATUS)),
            "dynpd": int(_read_register(spi, DYNPD)),
            "feature": int(_read_register(spi, FEATURE)),
            "rx_pw_p0": int(_read_register(spi, RX_PW_P0)),
            "rx_pw_p1": int(_read_register(spi, RX_PW_P1)),
            "rx_addr_p0": _read_register(spi, RX_ADDR_P0, 5),
            "rx_addr_p1": _read_register(spi, RX_ADDR_P1, 5),
        }
        result["registers"] = registers
        result["registers_hex"] = {
            key: [_hex(v) for v in value] if isinstance(value, list) else _hex(value)
            for key, value in registers.items()
        }
        result["details"] = {
            "model_hint": "nRF24L01+",
            "connected_hint": _looks_connected(registers),
            "channel_matches_config": registers["rf_ch"] == cfg["channel"],
            "payload_p1_matches_config": registers["rx_pw_p1"] == cfg["payload_size"],
            "legacy_host_default_ce_gpio": 22,
            "user_wiring_physical_22_ce_gpio": 25,
        }
        result["ok"] = bool(
            result["checks"]["spi_open"]["ok"]
            and result["checks"]["ce_gpio"]["ok"]
            and result["details"]["connected_hint"]
            and result["details"]["channel_matches_config"]
            and result["details"]["payload_p1_matches_config"]
        )
    except Exception as exc:
        result["error"] = f"radio_diag_error: {exc}"
    finally:
        try:
            spi.close()
        except Exception:
            pass
        _release_ce_gpio(ce_handle)
        result["duration_ms"] = round((time.time() - started) * 1000.0, 1)

    return result
