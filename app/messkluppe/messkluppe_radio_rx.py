from __future__ import annotations

import time
from typing import Any

try:
    from .messkluppe_radio_diag import (
        CONFIG,
        EN_AA,
        EN_RXADDR,
        DYNPD,
        FEATURE,
        FIFO_STATUS,
        NOP,
        RF_CH,
        RF_SETUP,
        RX_ADDR_P1,
        RX_PW_P1,
        SETUP_AW,
        SETUP_RETR,
        STATUS,
        _claim_ce_gpio,
        _read_register,
        _release_ce_gpio,
        _write_register,
        radio_diag_config_from_env,
    )
except ImportError:
    from messkluppe_radio_diag import (
        CONFIG,
        EN_AA,
        EN_RXADDR,
        DYNPD,
        FEATURE,
        FIFO_STATUS,
        NOP,
        RF_CH,
        RF_SETUP,
        RX_ADDR_P1,
        RX_PW_P1,
        SETUP_AW,
        SETUP_RETR,
        STATUS,
        _claim_ce_gpio,
        _read_register,
        _release_ce_gpio,
        _write_register,
        radio_diag_config_from_env,
    )


R_RX_PAYLOAD = 0x61
W_ACK_PAYLOAD = 0xA8
FLUSH_RX = 0xE2
FLUSH_TX = 0xE1

STATUS_RX_DR = 0x40
STATUS_TX_DS = 0x20
STATUS_MAX_RT = 0x10
FIFO_RX_EMPTY = 0x01

LEGACY_RX_ADDR_P1 = [0x71, 0xCD, 0xAB, 0xCD, 0xAB]


def _write_ce(handle: Any, value: int) -> None:
    if not handle:
        return
    backend, module, *tokens = handle
    if backend == "lgpio":
        chip, ce_gpio = tokens
        module.gpio_write(chip, ce_gpio, int(value))
    elif backend == "rpi_gpio":
        ce_gpio = tokens[0]
        module.output(ce_gpio, module.HIGH if value else module.LOW)


class MesskluppeRadioRx:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or radio_diag_config_from_env()
        self.spi: Any = None
        self.ce_handle: Any = None
        self.ce_check: dict[str, Any] = {}
        self.ready = False

    def __enter__(self) -> "MesskluppeRadioRx":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        import spidev  # type: ignore

        self.ce_check, self.ce_handle = _claim_ce_gpio(int(self.config["ce_gpio"]))
        if not self.ce_check.get("ok"):
            raise RuntimeError(f"ce_gpio_error: {self.ce_check}")

        self.spi = spidev.SpiDev()
        self.spi.open(int(self.config["spi_bus"]), int(self.config["spi_device"]))
        self.spi.max_speed_hz = int(self.config["spi_speed_hz"])
        self._configure_prx()
        self.ready = True

    def close(self) -> None:
        self.ready = False
        try:
            _write_ce(self.ce_handle, 0)
        except Exception:
            pass
        if self.spi is not None:
            try:
                self.spi.close()
            except Exception:
                pass
        _release_ce_gpio(self.ce_handle)
        self.spi = None
        self.ce_handle = None

    def _configure_prx(self) -> None:
        if self.spi is None:
            raise RuntimeError("spi_not_open")

        _write_ce(self.ce_handle, 0)
        _write_register(self.spi, CONFIG, 0x0B)
        _write_register(self.spi, EN_AA, 0x3F)
        _write_register(self.spi, EN_RXADDR, 0x03)
        _write_register(self.spi, SETUP_AW, 0x03)
        _write_register(self.spi, SETUP_RETR, 0x4F)
        _write_register(self.spi, RF_CH, int(self.config["channel"]))
        _write_register(self.spi, RF_SETUP, 0x05)
        _write_register(self.spi, RX_ADDR_P1, LEGACY_RX_ADDR_P1)
        _write_register(self.spi, RX_PW_P1, int(self.config["payload_size"]))
        _write_register(self.spi, FEATURE, 0x06)
        _write_register(self.spi, DYNPD, 0x3F)
        _write_register(self.spi, STATUS, STATUS_RX_DR | STATUS_TX_DS | STATUS_MAX_RT)
        self.spi.xfer2([FLUSH_RX])
        self.spi.xfer2([FLUSH_TX])
        time.sleep(0.005)
        _write_ce(self.ce_handle, 1)
        time.sleep(0.002)

    def read_payload(self) -> bytes | None:
        if self.spi is None:
            raise RuntimeError("spi_not_open")

        status = int(self.spi.xfer2([NOP])[0])
        fifo_status = int(_read_register(self.spi, FIFO_STATUS))
        if not (status & STATUS_RX_DR) and (fifo_status & FIFO_RX_EMPTY):
            return None

        payload_size = int(self.config["payload_size"])
        payload = bytes(self.spi.xfer2([R_RX_PAYLOAD, *([NOP] * payload_size)])[1 : payload_size + 1])
        _write_register(self.spi, STATUS, STATUS_RX_DR | STATUS_TX_DS | STATUS_MAX_RT)
        return payload

    def write_ack_payload(self, payload: bytes, *, pipe: int = 1, flush_tx: bool = True, pad_to: int | None = None) -> dict[str, Any]:
        if self.spi is None:
            raise RuntimeError("spi_not_open")
        if pipe < 0 or pipe > 5:
            raise ValueError("pipe must be between 0 and 5")
        raw = bytes(payload)
        if not raw or len(raw) > 32:
            raise ValueError("ack payload must contain 1..32 bytes")
        if pad_to is not None:
            size = max(len(raw), min(32, int(pad_to)))
            raw = raw.ljust(size, b"\x00")

        if flush_tx:
            self.spi.xfer2([FLUSH_TX])
        status_before = int(self.spi.xfer2([NOP])[0])
        response = self.spi.xfer2([W_ACK_PAYLOAD | pipe, *raw])
        status_after = int(self.spi.xfer2([NOP])[0])
        return {
            "ok": True,
            "pipe": pipe,
            "payload_hex": raw.hex(),
            "size": len(raw),
            "status_before": status_before,
            "status_after": status_after,
            "response_status": int(response[0]) if response else None,
        }

    def snapshot(self) -> dict[str, Any]:
        if self.spi is None:
            return {"ready": False, "configured": self.config, "ce_gpio": self.ce_check}
        return {
            "ready": self.ready,
            "configured": self.config,
            "ce_gpio": self.ce_check,
            "status": int(self.spi.xfer2([NOP])[0]),
            "fifo_status": int(_read_register(self.spi, FIFO_STATUS)),
        }
