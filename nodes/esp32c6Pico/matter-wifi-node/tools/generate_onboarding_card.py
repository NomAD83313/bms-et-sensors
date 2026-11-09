#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont


CARD_W = 1748
CARD_H = 1460
MARGIN = 78
QR_SIZE = 560
BASE38_CODES = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-."
BASE38_CHARS_NEEDED = [2, 4, 5]
VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        path = Path(name)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def draw_text(draw, xy, text, fill, size, bold=False):
    draw.text(xy, text, fill=fill, font=font(size, bold))


def text_width(draw, text, selected_font):
    left, _, right, _ = draw.textbbox((0, 0), text, font=selected_font)
    return right - left


def draw_text_fit(draw, xy, text, fill, size, max_width, bold=False, min_size=24):
    selected_size = size
    selected_font = font(selected_size, bold)
    while selected_size > min_size and text_width(draw, text, selected_font) > max_width:
        selected_size -= 1
        selected_font = font(selected_size, bold)
    draw.text(xy, text, fill=fill, font=selected_font)


def draw_field(draw, x, y, label, value, max_width=None):
    draw_text(draw, (x, y), label.upper(), "#637083", 28, True)
    if max_width:
        draw_text_fit(draw, (x, y + 34), value, "#111827", 40, max_width)
    else:
        draw_text(draw, (x, y + 34), value, "#111827", 40, False)
    return y + 100


def draw_led_patterns(draw, x, y, width, patterns):
    draw_text(draw, (x, y), "RGB LED STATUS", "#637083", 28, True)
    cols = 3
    gap = 18
    box_w = (width - gap * (cols - 1)) // cols
    box_h = 104
    start_y = y + 50

    for index, item in enumerate(patterns):
        parts = [part.strip() for part in item.split(";")]
        if len(parts) == 3:
            pattern, color, meaning = parts
        elif len(parts) == 2:
            pattern, meaning = parts
            color = "#cbd5e1"
        else:
            pattern = item.strip()
            color = "#cbd5e1"
            meaning = ""

        row = index // cols
        col = index % cols
        box_x = x + col * (box_w + gap)
        box_y = start_y + row * (box_h + gap)

        draw.rounded_rectangle(
            (box_x, box_y, box_x + box_w, box_y + box_h),
            radius=16,
            fill="#f8fafc",
            outline="#d9e2ec",
            width=2,
        )
        draw.rounded_rectangle(
            (box_x + 16, box_y + 18, box_x + 52, box_y + 54),
            radius=10,
            fill=color,
            outline="#94a3b8",
            width=1,
        )
        draw_text(draw, (box_x + 66, box_y + 14), pattern.upper(), "#4b5b73", 21, True)
        draw_text(draw, (box_x + 20, box_y + 54), meaning, "#111827", 27, False)


def make_qr(payload):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=14,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#111827", back_color="white").convert("RGB")
    return img.resize((QR_SIZE, QR_SIZE), Image.Resampling.NEAREST)


def parse_int(value):
    head = value.strip().split()[0]
    return int(head, 0)


def base38_encode(data):
    encoded = ""
    for offset in range(0, len(data), 3):
        chunk = data[offset:offset + 3]
        value = sum(byte << (8 * index) for index, byte in enumerate(chunk))
        for _ in range(BASE38_CHARS_NEEDED[len(chunk) - 1]):
            encoded += BASE38_CODES[value % 38]
            value //= 38
    return encoded


def generate_qr_payload(passcode, discriminator, vendor_id, product_id, discovery=2):
    fields = [
        (0, 3),
        (vendor_id, 16),
        (product_id, 16),
        (0, 2),
        (discovery, 8),
        (discriminator, 12),
        (passcode, 27),
        (0, 4),
    ]
    bits = []
    for value, width in fields:
        bits.extend((value >> index) & 1 for index in range(width))

    data = bytearray(11)
    for index, bit in enumerate(bits):
        if bit:
            data[index // 8] |= 1 << (index % 8)
    return "MT:" + base38_encode(data)


def verhoeff_check_digit(payload):
    checksum = 0
    for index, digit in enumerate(reversed(payload)):
        checksum = VERHOEFF_D[checksum][VERHOEFF_P[(index + 1) % 8][int(digit)]]
    return str(VERHOEFF_INV[checksum])


def bits_to_int(bits):
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


def generate_manual_code(passcode, discriminator):
    fields = [
        (0, 1),
        (0, 1),
        (discriminator >> 8, 4),
        (passcode & 0x3FFF, 14),
        (passcode >> 14, 13),
    ]
    bits = []
    for value, width in fields:
        bits.extend((value >> index) & 1 for index in reversed(range(width)))

    payload = (
        str(bits_to_int(bits[0:4])).zfill(1)
        + str(bits_to_int(bits[4:20])).zfill(5)
        + str(bits_to_int(bits[20:33])).zfill(4)
    )
    return payload + verhoeff_check_digit(payload)


def main():
    parser = argparse.ArgumentParser(description="Generate a printable Matter onboarding card.")
    parser.add_argument("--output-png", required=True)
    parser.add_argument("--qr")
    parser.add_argument("--manual-code")
    parser.add_argument("--passcode", required=True)
    parser.add_argument("--discriminator", required=True)
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--firmware", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--endpoints", required=True)
    parser.add_argument("--led-patterns", default="")
    args = parser.parse_args()

    output = Path(args.output_png)
    output.parent.mkdir(parents=True, exist_ok=True)
    passcode = parse_int(args.passcode)
    discriminator = parse_int(args.discriminator)
    vendor_id = parse_int(args.vendor_id)
    product_id = parse_int(args.product_id)
    qr_payload = args.qr or generate_qr_payload(passcode, discriminator, vendor_id, product_id)
    manual_code = args.manual_code or generate_manual_code(passcode, discriminator)

    card = Image.new("RGB", (CARD_W, CARD_H), "white")
    draw = ImageDraw.Draw(card)

    draw.rectangle((0, 0, CARD_W, 18), fill="#2563eb")
    draw_text(draw, (MARGIN, 68), "BMS Matter Node", "#111827", 72, True)
    draw_text(draw, (MARGIN, 154), args.product, "#334155", 38)

    qr_img = make_qr(qr_payload)
    qr_x = CARD_W - MARGIN - QR_SIZE
    qr_y = 120
    draw.rounded_rectangle((qr_x - 28, qr_y - 28, qr_x + QR_SIZE + 28, qr_y + QR_SIZE + 28),
                           radius=18, fill="#f8fafc", outline="#cbd5e1", width=3)
    card.paste(qr_img, (qr_x, qr_y))
    draw_text(draw, (qr_x, qr_y + QR_SIZE + 38), "Scan in Google Home / Matter controller", "#334155", 29)

    y = 255
    y = draw_field(draw, MARGIN, y, "QR payload", qr_payload)
    y = draw_field(draw, MARGIN, y, "Manual pairing code", manual_code)
    y = draw_field(draw, MARGIN, y, "Setup passcode", args.passcode)
    y = draw_field(draw, MARGIN, y, "Discriminator", args.discriminator)

    left_y = 820
    draw.line((MARGIN, left_y - 36, CARD_W - MARGIN, left_y - 36), fill="#e2e8f0", width=3)
    col2 = MARGIN + 520
    col3 = MARGIN + 1040
    draw_field(draw, MARGIN, left_y, "Serial pattern", args.serial, 460)
    draw_field(draw, col2, left_y, "Firmware", args.firmware, 460)
    draw_field(draw, col3, left_y, "Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), 460)

    draw_field(draw, MARGIN, 940, "VID / PID", f"{args.vendor_id} / {args.product_id}")
    draw_field(draw, col2, 940, "Transport", "Wi-Fi + BLE")
    draw_field(draw, col3, 940, "Endpoints", args.endpoints)

    if args.led_patterns:
        draw_led_patterns(
            draw,
            MARGIN,
            1050,
            CARD_W - 2 * MARGIN,
            [p for p in args.led_patterns.split("|") if p.strip()],
        )

    draw_text(draw, (MARGIN, CARD_H - 76),
              "Lab firmware: test VID/PID. For production use per-device factory data and certified credentials.",
              "#64748b", 26)

    card.save(output)
    print(output)


if __name__ == "__main__":
    main()
