# Vibration sensors (WitMotion WTVB01-485)

`vibration-collector` polls WitMotion WTVB01-485 three-axis vibration sensors on one
RS485 bus through a USB adapter and writes every reading to InfluxDB. It runs in the
Compose profile `vibration` and does not affect the default stack.

Reference: [WTVB01-485 Data Sheet & User Manual V260508](witmotion/WTVB01-485-user-manual.pdf),
section 10 (Modbus protocol).

## Start

```bash
./scripts/build-local-vibration.sh
```

Then open `http://<host>/vibration/`:

1. **Bus**: select the adapter under `/dev/serial/by-id/` and **Save**. The port is never
   chosen automatically, because other collectors share the host's USB serial ports.
2. **Scan**: search addresses 0x01–0x7F at the selected baud rates (about 10 s per rate at
   9600 baud) and **Add** the sensors found, or add a sensor by address.
3. Readings appear in the sensor table; **All values** shows every decoded field.

Health shows a **Vibration (WTVB01)** card and a Start/Stop control (`vibration` target of
service-controller). The registry is `runtime/vibration/config.json` (runtime state, not
tracked).

## Hardware

- Supply 9–36 V (12 V typical, about 8 mA); RS485 A/B to the adapter.
- Defaults: 9600 baud, 8N1, Modbus address 0x50. Rates 4800–230400 baud; addresses 0x01–0x7F.
- Up to 32 sensors per bus; every sensor on a bus must use the same baud rate and a unique
  address. Change the address of each new sensor before connecting the next one with the
  default address.
- The vibration values need at least 5 Hz vibration to be reliable (manual section 3.1).
- The tested adapter is a CH340 (`1a86:7523`). MSCL and ALMEMO autodetection probe every
  USB serial port, so `1a86:7523` is in their `*_EXCLUDE_USB_IDS` defaults; without it MSCL
  sends base-station pings into the RS485 bus and corrupts sensor replies. A CH340 has no
  serial number: with two identical adapters, use distinct USB ports and verify the selected
  by-id/by-path entry.

## Data

One request per register block, function 0x03. Read profiles:

| Profile | Registers | Fields | Read time at 9600 baud |
|---|---|---|---|
| `basic` | 0x34–0x46 | acceleration, velocity, temperature, displacement, frequency | ~60 ms |
| `extended` (default) | + 0x47–0x6D | statistics per axis and fault codes | ~165 ms |
| `full` | + 0x6E–0x9A | spectrum energy: 8 focus points, 6 bands and total per axis | ~285 ms |

Measured on one sensor with a CH340 adapter: 16.3 / 6.0 / 3.5 reads per second for the three
profiles at 9600 baud and no errors over 578 reads; at 115200 baud a `full` cycle takes about
36 ms. Above 115200 adapter and sensor latency dominate, so 230400 gains little and is less
tolerant of long cables.

Internal refresh rates, measured by polling one sensor at 165 reads/s (115200 baud):

| Block | Refresh in the sensor | Collector behaviour |
|---|---|---|
| velocity, displacement, temperature (0x3A–0x43) | faster than the bus can read | read every cycle |
| acceleration (0x34–0x36) | no change in 10 s at rest (heavily smoothed) | read every cycle |
| statistics (0x47–0x6D) | every 100 ms | read at most every 0.1 s |
| spectrum energy (0x6E–0x9A) | about every 1 s | read at most every 1 s |

Skipped blocks keep their last values in the UI but are not written to InfluxDB again, so a
poll interval below 0.1 s only adds basic-block samples. For trend-based diagnosis a poll
interval of 0.5–1 s is enough.

InfluxDB measurement `wtvb01` (override with `VIBRATION_INFLUX_MEASUREMENT`), tags `sensor`,
`address`, `source=wtvb01`, one float field per value:

| Field | Register | Scaling (manual) |
|---|---|---|
| `acc_{x,y,z}_g` | 0x34–0x36 | signed / 32768 × 16 g |
| `velocity_{x,y,z}_mm_s` | 0x3A–0x3C | / 100 mm/s |
| `temperature_c` | 0x40 | signed / 100 °C (chip temperature) |
| `displacement_{x,y,z}_um` | 0x41–0x43 | µm |
| `frequency_{x,y,z}_hz` | 0x44–0x46 | / 10 Hz |
| `crest_factor_`, `kurtosis_`, `acc_abs_mean_`, `acc_variance_`, `acc_rms_`, `waveform_index_`, `peak_index_`, `pulse_index_`, `skewness_`, `velocity_rms_`, `velocity_kurtosis_`, `displacement_rms_` (per axis) | 0x47–0x52, 0x53–0x5E, 0x5F–0x6A | / 1000 |
| `fault_{x,y,z}` | 0x6B–0x6D | raw |
| `energy_{x,y,z}_point1..8`, `_band1..6`, `_total` | 0x6E–0x9A | raw |

Deliberate interpretations of the manual:

- The summary table lists 0x50 as PIX, but the detailed section places CFX…DRMSX
  contiguously at 0x47–0x52, like the Y and Z blocks; the detailed layout is used.
- Velocity is specified up to 600 mm/s with a /100 scale (60000 raw), which does not fit a
  signed 16-bit value, so non-negative quantities are decoded unsigned. Acceleration,
  temperature, kurtosis and skewness are signed (a real sensor returned skewness 65531 = −0.005).
- The summary table gives displacement in mm; the detailed section and the 0–30000 µm range
  give µm, which is used.
- The sensor occasionally returns a statistics block computed over a transient: velocity or
  displacement RMS reads 0xFFFF (65.535) and the other statistics are meaningless (kurtosis
  around ±30). The collector discards such a block, keeps the previous values in the UI, reads
  it again on the next cycle and counts it as `rejected_blocks` in `/api/status`.
- On a sensor at rest the statistics are noisy (for example kurtosis on the gravity axis);
  validate thresholds under real machine vibration.

## Harmonics

The sensor computes its own spectrum (about once per second) and reports energy at eight focus
points relative to the fundamental vibration frequency (1x), six bands and the total, per axis.
Register polling gives tens of irregular samples per second, far below the uniform sampling
an FFT needs; the collector's own spectrum uses the raw FIFO instead (next section).

| Focus point | Harmonic | Typical meaning |
|---|---|---|
| point1 / point2 | 0.25x / 0.5x | rub, oil whirl, looseness |
| point3 | 1x | unbalance |
| point4 | 2x | misalignment, looseness |
| point5–8 | 3x–6x | looseness, misalignment, blade/vane pass |

Bands 1–6 cover 0–0.5x, 0.5–1x, 1–2x, 2–3x, 3–4x and 4–5x. With profile `full` the collector
also writes `energy_{x,y,z}_ratio_2x_1x` and `energy_{x,y,z}_ratio_half_1x` (omitted while 1x
energy is zero, for example at rest). Energy units are not documented, so compare each machine
with its own normal operation rather than with absolute limits.

The manual does not say which frequency the sensor takes as 1x, and it cannot be configured.
Field tests suggest a fixed reference of about 50 Hz, unrelated to the machine's rotation: a
washing machine (wash, rinse and spin) and a tumble dryer, two different machines, both gave
the same pattern, with a dominant frequency of 100 Hz, point3 (1x) energy close to zero, point4
(2x) around 8, most energy in bands 3–4 (1–3x) and nothing above 3x. Treat the ratios as
experimental. For diagnosis, rely on total energy, the band distribution per axis, velocity
RMS, kurtosis and crest factor. The 5–100 Hz range covers
unbalance, misalignment and looseness up to about 6000 rpm; rolling-bearing defect frequencies
are higher, and only kurtosis and crest factor hint at them indirectly.

The UI shows a **Harmonics** panel per sensor. Grafana provisions the dashboard
**Vibration (WTVB01-485)** (`/grafana/d/bms_vibration`, file
`grafana/provisioning/dashboards/vibration_dash.json`) with velocity RMS, harmonic energy and
ratios, kurtosis/crest factor, frequency, total energy and temperature per selected sensor.

## Raw acceleration FIFO and FFT

Register 0x2C is not in manual V260508; the command came from WitMotion support and the
behaviour below was measured on a WTVB01-485 at 115200 and 230400 baud:

- `50 03 00 2C 00 01 48 42` starts a capture. There is no immediate reply.
- While capturing, the sensor sends progress frames `50 03 01 NH NL CRC` by itself about every
  250 ms; N counts bytes, 3 + 6 × samples. Occasionally a frame repeats about 30 ms later.
- After about 4.2 s it sends `50 03 00` + 1024 × (AX AY AZ, int16 big-endian) + CRC, 6149 bytes:
  0.53 s on the wire at 115200 baud, 0.27 s at 230400, about 6.4 s at 9600. Capture time does
  not depend on the baud rate. Afterwards the sensor is silent until the next request.
- A request during a capture does not restart it.
- Scale 16 g / 32768 as registers 0x34–0x36, gravity included, but the sign is inverted on all
  three axes; the collector flips it back.
- Sample rate is not documented; measured 245.6–246.4 samples/s, so one capture covers about
  4.16 s with 0.24 Hz resolution and a Nyquist frequency of about 123 Hz.

The collector measures the sample rate of every capture from the progress frames (falling back
to 246 Hz, shown as "default") and computes per axis: mean removed, Hann window, amplitude
spectrum in g and velocity spectrum in mm/s, the three strongest peaks above 2 Hz, band RMS in
g (2–10, 10–25, 25–50, 50–100, 100 Hz–Nyquist) and velocity RMS from 10 Hz to Nyquist.

Because the sensor transmits unprompted during a capture, a capture is an exclusive bus
operation like a scan: polling of every sensor pauses for about 4.5 s. Within an averaged
series every sensor is polled once between two captures (the sensor is silent then), so live
values and the `wtvb01` series in InfluxDB keep updating about every 4.5 s instead of
stopping for the whole series. Live values keep their last reading during a capture and the
sensor stays online.

- **On demand**: per sensor, **Spectrum (FFT) → Capture spectrum now** in the UI, or
  `POST /vibration/api/sensors/<address>/spectrum`; the last full spectrum is at
  `GET /vibration/api/sensors/<address>/spectrum` (kept in memory only).
- **Periodic**: **Spectrum (FFT) → Periodic capture**: interval in seconds (5–86400),
  **Start** / **Apply** / **Stop**. It captures every enabled, online sensor in turn; the
  interval counts from the end of a capture series, so polling runs in between. The state
  and the time to the next capture are shown there and in the **Periodic FFT** tile. FFT
  settings can change while a capture runs; **Stop** also ends a running periodic series
  after the current capture and keeps the spectrum of the captures already taken. Config
  keys `fft_periodic` (on/off) and `fft_interval_sec` (default 300, kept while stopped); an
  older file with `fft_interval_sec: 0` loads as stopped.

**Averaging.** One capture is always 1024 samples (4.16 s, 0.24 Hz resolution). Captures cannot
be joined into a longer record: the sensor stops sampling between them and the phase after the
gap is unknown, so joined records would add false lines. Instead several captures are taken
in a row, each is transformed on its own and the amplitudes are averaged in power (RMS
averaging). Resolution stays 0.24 Hz; steady lines keep their amplitude while random noise
and transients are smoothed (the noise floor fluctuates about √N less). The **Average** selector in
the spectrum panel (`fft_averages`, 1–16, default 1) applies to manual and periodic captures;
the API also takes `{"averages": N}` in the POST body for one capture. Polling pauses for about 4.5 s per capture (16 captures: about 72 s); the UI shows
`capture N/M`, and one failed capture fails the whole series. The summary field `averages`
records the count.

Every capture writes one summary point to InfluxDB measurement `wtvb01_fft` (override with
`VIBRATION_FFT_INFLUX_MEASUREMENT`), same tags as `wtvb01`, fields `sample_rate_hz`, `averages`,
`peak{1..3}_{x,y,z}_hz`, `peak{1..3}_{x,y,z}_g`, `band_{x,y,z}_{2_10,10_25,25_50,50_100,100_nyq}_g`
and `velocity_rms_{x,y,z}_mm_s`. The full spectrum is not stored.

The Grafana dashboard **Vibration (WTVB01-485)** has a section **Spectrum (FFT) · raw FIFO
captures** built from `wtvb01_fft`: last main peak, its amplitude and velocity RMS per axis;
velocity RMS from FFT over time; the three strongest peaks per axis as points against the
expected roll 1x/2x/3x (dashed, from the line speed and roll diameter variables); main peak
amplitude; band RMS stacked per axis (bands numbered 1–5 from low to high frequency); and the
sample rate and number of averaged captures of the last series. Each capture series is one
point, so these panels need periodic capture to show trends.

## Sensor configuration

Writes follow the manual: unlock (0x69 = 0xB588, valid 10 s) → write → save (0x00 = 0x0000).

- **Set address** changes one sensor; it answers at the new address immediately, so the
  unlock and save go to the new address. The registry entry follows.
- **Change sensor baud** writes the new rate into every registered sensor, saves it at the new
  rate, switches the port and stores the new port baud. Sensors that miss the change stay at
  the old rate; scan at that rate to find them.
- **Port baud** in the Bus form changes only the adapter, for sensors already at that rate.

Polling pauses while a scan or configuration change owns the bus.

## Troubleshooting

- `serial I/O error … multiple access on port`: another process opened the adapter. Check
  `docker logs mscl-collector` / `almemo-collector` and their `*_EXCLUDE_USB_IDS`.
- The first request after opening the port can be lost on a CH340; the collector waits 200 ms
  after opening and retries a lost or corrupted reply once after the line is quiet.
- `./scripts/logs.sh vibration-collector`; status JSON at `http://<host>/vibration/api/status`.
