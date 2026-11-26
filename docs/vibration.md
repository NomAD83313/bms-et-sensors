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
- Kurtosis and skewness divide by the squared variance. On an axis that carries gravity
  (|mean| about 1 g) and is at rest, the sensor reports variance 0.000 and returns overflow
  noise of either sign (kurtosis −29.7 or +28.8, skewness 25.5); the other axes stay valid, and
  an axis near 0 g with variance 0.000 is fine. The collector drops `kurtosis_`, `skewness_` and
  `velocity_kurtosis_` of an axis when its variance is 0 with |mean| ≥ 0.5 g, or when Pearson's
  inequality kurtosis ≥ skewness² + 1 fails (this also covers kurtosis below 1). The rest of
  the block is kept and the axis is counted as `invalid_shape_axes` in `/api/status`. On a
  2-hour washing machine record this removed 537 of 1605 Y samples (7 while running) and none
  on X/Z. Velocity kurtosis legitimately reads below 1 (0.8–1.3 on a running machine) and is
  not used as a criterion.
- On a sensor at rest the statistics are noisy (for example kurtosis on the gravity axis);
  validate thresholds under real machine vibration.

## Sensor spectrum energy

The sensor reports its own spectrum energy at eight focus points, six focus bands and a total
per axis. The operator interface and Grafana identify these fields by number. The reference
frequency and energy units for the WTVB01 firmware are unconfirmed, so comparisons are most useful
against the machine baseline. Use the raw FIFO spectrum for frequencies in Hz.

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
g (2–10, 10–25, 25–50, 50–100, 100 Hz–Nyquist) and velocity RMS from a lower limit to Nyquist.

- **Velocity RMS lower limit** (`fft_velocity_min_hz`, config/API): 2 Hz by default; set
  it to 10 Hz to use that lower cutoff. Older configurations with a value of 0 load as 2 Hz.
  Use the same cutoff when comparing historical RMS values.
- **Waveform and orbits** (UI tabs *Waveform* and *Orbit*, `GET /vibration/api/sensors/<address>/waveform`,
  last capture only, in memory): acceleration (g, mean removed), velocity (mm/s) and displacement (µm)
  per axis. Integration uses a 2 Hz lower bound to limit drift. The broadband displacement plots
  show XY/XZ/YZ and their minor-to-major axis ratios.
- For low-frequency vibration near or below the specified range of the live sensor registers,
  inspect the raw FIFO spectrum and verify its response with a known test signal.

Because the sensor transmits unprompted during a capture, a capture is an exclusive bus
operation like a scan: polling of every sensor pauses for about 4.5 s. Within an averaged
series every sensor is polled once between two captures (the sensor is silent then), so live
values and the `wtvb01` series in InfluxDB keep updating about every 4.5 s instead of
stopping for the whole series. Live values keep their last reading during a capture and the
sensor stays online.

- **On demand**: per sensor, **Spectrum (FFT) → Capture spectrum now** in the UI, or
  `POST /vibration/api/sensors/<address>/spectrum`; the last full spectrum is at
  `GET /vibration/api/sensors/<address>/spectrum` (kept in memory only).
  With **Save record** checked next to the button, the capture is started through
  `POST /vibration/api/sensors/<address>/records` instead and is stored as a record shown in
  Graf App Lite (`/graf/vibration`).
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
the spectrum panel (`fft_averages`, 1–16, default 1) applies to on-demand and periodic captures;
the API also takes `{"averages": N}` in the POST body for one capture. Polling pauses for about 4.5 s per capture (16 captures: about 72 s); the UI shows
`capture N/M`, and one failed capture fails the whole series. The summary field `averages`
records the count.

**Saved time signals in Graf App Lite.** The `/graf/vibration` page can start a named capture
for any enabled sensor with 1, 2, 4, 8 or 16 FFT averages. Each record stores the signed,
mean-removed acceleration, velocity and displacement waveform from the last FIFO capture,
the averaged FFT, and the raw acceleration FIFO data for every average. Records are gzip JSON
files under `runtime/vibration/records/`; this is runtime state and is not committed. The table
can reopen a record, draw its waveforms, orbits and acceleration FFT, download JSON or CSV, and
delete it. Multiple records can be selected and deleted together. API routes are `POST /vibration/api/sensors/<address>/records`,
`GET /vibration/api/records`, and `GET`/`DELETE /vibration/api/records/<id>`; append
`/download.csv` or `/download.json` for exports.

The record's **CSV** button opens Graf's export dialog with the same locale options as the other
exports. **Saved waveform as shown** exports the three signed waveforms at their saved sample rate.
**Raw acceleration (all captures)** exports each FIFO capture separately, with elapsed time from
the start of each capture estimated using the saved rate. **Saved waveform at selected rate** averages
the signed waveform in time bins at the requested rate, up to the saved sample rate.

The same panel can start automatic recording using the existing periodic FFT scheduler. Configure
the interval, FFT averages, retention age and maximum record count, then select **Start automatic
recording**. Each completed periodic capture is stored in the same table with source **Automatic**;
records beyond either retention limit are deleted oldest first. **Stop automatic recording** stops
the periodic scheduler and automatic persistence. The related config keys are `fft_save_records`,
`record_retention_days` (default 7) and `record_max_count` (default 100).
Starting or stopping periodic capture in the vibration collector UI also enables or disables record
persistence, so both interfaces report and control the same timer state.

**Continuous recording** (same panel, 10–600 s) requests FIFO captures of one sensor back to back
and saves them as **one** record (`kind: "burst"`). Polling of all sensors pauses for the whole
record, so live values and the `wtvb01` series in InfluxDB stop meanwhile; the next request follows
the data frame immediately. The gap between captures is about the data frame's transfer time
(0.27 s at 230400 baud, 0.53 s at 115200), so about 93 % of the time is covered at 230400 baud.
The last capture ends after the requested time. **Stop** ends the record after the current capture,
and a failed capture ends it early; both keep the captures already taken. `POST
/vibration/api/sensors/<address>/burst` with `{"duration_s": 60, "note": "..."}` starts it and `POST
/vibration/api/operation/stop` stops it (this also stops an averaged series).

The first-sample time of every capture is the bus clock at the request plus the offset fitted from
the progress frames. The serial/USB latency in that offset is nearly constant, so gaps are accurate to
a few ms. The record stores these start times (`capture_starts_s`), the measured rate of every capture,
`span_s`, `coverage` and gap statistics. Captures are still never joined: the gaps and the timing
uncertainty would add false lines, so frequency resolution stays 0.24 Hz per capture. Each capture is
integrated on its own. The record's averaged spectrum and last waveform update the latest spectrum
and write one `wtvb01_fft` point like an averaged series.

**View timeline + FFT** opens the analysis (`GET /vibration/api/records/<id>/burst?window=256|512|1024`,
computed on demand and cached in memory; about 6 s on a Raspberry Pi 5 for 600 s): min/max envelope of
acceleration, velocity or displacement over the whole record (in the recorder panel); below the orbit panel a per-capture trend (velocity RMS,
acceleration RMS or peak, crest factor, dominant frequency), the three strongest FFT peaks per axis and
capture as a frequency-over-time chart, and a spectrogram (Hann windows of 256/512/1024 samples with
50 % overlap inside each capture; 256 gives about 1 s and 1 Hz). Gaps are shaded. Clicking a chart selects
a capture: its waveform and orbits replace the live panels (**Show live capture** returns) and its FFT is
shown below (by default the last capture is selected); the FFT selector also offers the average of all captures and the maximum of all captures (highest amplitude per frequency, also called peak hold)
(`GET /vibration/api/records/<id>/captures/<n>` serves any record's capture n). Every capture also stores the wall-clock time of its first sample (`capture_started_at`; the waveform's `started_at`), and time axes show it as their first label. The raw CSV export adds
`record_time_s` (time since the record start, gaps included). A 600 s record is about 1 MB.

The recorder deliberately labels the acquisition limits instead of presenting unsupported
controls: the WTVB01 FIFO length is fixed at 1024 points and its sampling rate is sensor-controlled
(measured for every capture). Therefore selecting 2048/4096 points or changing the sampling rate
is not possible with the known protocol. FFT averaging improves stability but does not extend the
record duration or change frequency resolution.

Every capture series writes one summary point to InfluxDB measurement `wtvb01_fft` (override with
`VIBRATION_FFT_INFLUX_MEASUREMENT`). It includes the sample rate, averages, the three strongest
FFT peaks per axis in Hz and g, fixed-band RMS in g, velocity RMS and its lower limit, and
broadband displacement orbit ratios. The full spectrum is held in memory.

The Grafana dashboard **Vibration (WTVB01-485)** displays the dominant frequency reported by
sensor registers, and separately the **FFT peak frequencies** in Hz from the raw data. The FFT
section also shows peak amplitudes, fixed-band RMS, velocity RMS and capture settings. Use periodic
capture to collect these trends.
**Orbit axis ratio (FFT)** shows the historical minor-to-major ratio for XY, XZ and YZ. The scale
runs from 0 (line) to 1 (circle). The collector UI's **Orbit** tab shows the latest displacement paths.

In the collector UI, **Compare** keeps the scale per sensor, chart and unit while the page is
open. A larger capture expands it and **Reset scale** fits it again using the current capture and
the starting range. **Auto** fits each capture. Grafana uses soft axis bounds to give small
readings a common visual scale and expand for larger readings. **FFT peak frequencies** uses
individual points and shows the matching peak amplitudes in the tooltip. Display ranges are
independent of any diagnostic limits.

## Simulation (test session without a machine)

### Simulated roll sensors (next to the real bus)

The **Sensor** selector on `/graf/vibration` always offers two simulated sensors:
`sim-roll-600-defects` (address 127, `VIBRATION_SIM_SENSOR_ADDRESS`) and
`sim-roll-600-healthy` (address 126, `VIBRATION_SIM_HEALTHY_ADDRESS`). Selecting one starts it
and stops the other; selecting a real sensor stops the running one (`POST
/vibration/api/sim-sensor` with `{"address": 127, "active": true|false}`; `GET
/vibration/api/status` reports `sim_sensors`). A running simulator is a registered sensor: its
frames go to the simulator, never to RS485, so the real sensors keep working. It is polled,
written to InfluxDB under its name, and supports spectra, records and continuous recording with
the real FIFO timing (4.2 s per capture). It needs the real serial port to be present, because
polling opens it first.

Both model (`vibration_sim.RollDriveSimulator`) a 600 mm roll on a direct drive. Line speed
alternates between 450 and 600 m/min every 30 s with 5 s ramps (25 s hold), counted from
switching the sensor on, so the roll turns at 3.98 or 5.31 Hz. The signal phase follows the
roll angle, so consecutive captures continue smoothly and frequencies sweep during the ramps.
Noise is 0.6 mg RMS per axis.

- **defects**: a small unbalance gives a 1x line (peak velocity X 1.2, Y 0.9, Z 0.2 mm/s at
  600 m/min, scaling with speed²) with an elliptical XY orbit, plus small 2x and 3x. A small
  outer-race defect in the drive bearing (10-ball deep-groove bearing, BPFO 4.12x: 16.4 Hz at
  450 m/min, 21.9 Hz at 600) gives impacts that ring a 72 Hz housing resonance, mostly on Y, so
  the spectrum shows BPFO harmonics around 72 Hz.
- **healthy**: well balanced (residual 1x X 0.15, Y 0.12, Z 0.04 mm/s at 600 m/min, tiny 2x/3x)
  and a healthy bearing (no impacts): the baseline to compare the defective roll with.

Set `VIBRATION_SIM_PORTS=1` in `.env` and rebuild the collector. The Bus form offers
`sim://frequency?hz=5.25` and a 30-minute fault scenario (`&scenario=faults`). The optional
`hz` parameter sets the reference frequency of the synthetic signal. The simulator uses the
real serial protocol and timing for one sensor at address 0x50, including FIFO progress frames.
The synthetic signal includes harmonic components, a fixed 24.5 Hz line on Z, gravity and noise.
Use a distinct sensor name for simulation and restore the real port and name when finished.

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
