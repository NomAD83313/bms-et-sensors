"""In-memory WTVB01-485 bus used by the vibration tests."""

from app.vibration import wtvb01_protocol as proto


def _with_crc(body):
    crc = proto.crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def fifo_progress_frame(address, samples):
    count = 3 + 6 * samples
    return _with_crc(bytes((address, proto.FUNC_READ, proto.FIFO_PROGRESS_MARK, count >> 8, count & 0xFF)))


def fifo_data_frame(address, raw_samples):
    """raw_samples: (ax, ay, az) signed int16 as the sensor sends them."""
    payload = b"".join(v.to_bytes(2, "big", signed=True) for sample in raw_samples for v in sample)
    return _with_crc(bytes((address, proto.FUNC_READ, proto.FIFO_DATA_MARK)) + payload)


class FakeSensor:
    def __init__(self, address=0x50, baud=9600, registers=None):
        self.address = address
        self.baud = baud
        self.registers = dict(registers or {})
        self.registers.setdefault(proto.REG_ADDRESS, address)
        self.registers.setdefault(proto.REG_BAUD, proto.BAUD_CODES[baud])
        self.unlocked = False
        self.saved = 0
        # Raw FIFO content: 1024 (ax, ay, az) int16 triples; None keeps the sensor capturing forever.
        self.fifo = [(0, 0, -1961)] * proto.FIFO_SAMPLES
        self.fifo_progress = (62, 124, 186)
        self.fifo_corrupt = False
        self.fifo_requests = 0

    def _fifo_reply(self):
        """Progress frames then the data frame, as the sensor sends them unprompted."""
        self.fifo_requests += 1
        reply = b"".join(fifo_progress_frame(self.address, n) for n in self.fifo_progress)
        if self.fifo is None:
            return reply
        data = bytearray(fifo_data_frame(self.address, self.fifo))
        if self.fifo_corrupt:
            data[100] ^= 0xFF
        return reply + bytes(data)

    def handle(self, frame):
        """Return the reply bytes, or b'' when the sensor stays silent."""
        if len(frame) < 8 or frame[0] != self.address or proto.crc16(frame[:-2]) != frame[-2] | (frame[-1] << 8):
            return b""
        function, register, value = frame[1], (frame[2] << 8) | frame[3], (frame[4] << 8) | frame[5]
        if function == proto.FUNC_READ and register == proto.REG_FIFO:
            return self._fifo_reply()
        if function == proto.FUNC_READ:
            words = [self.registers.get(register + i, 0) for i in range(value)]
            body = bytes((self.address, proto.FUNC_READ, 2 * value)) + b"".join(w.to_bytes(2, "big") for w in words)
            crc = proto.crc16(body)
            return body + bytes((crc & 0xFF, crc >> 8))
        if function != proto.FUNC_WRITE:
            return b""
        if register == proto.REG_UNLOCK and value == proto.UNLOCK_KEY:
            self.unlocked = True
            return frame
        if not self.unlocked:
            return frame  # Echoed but ignored, as a locked device would.
        if register == proto.REG_SAVE:
            self.saved += 1
            self.unlocked = False
            return frame
        if register == proto.REG_ADDRESS:
            self.registers[register] = value
            self.address = value  # Takes effect immediately (manual 10.4.3 example).
            return frame
        if register == proto.REG_BAUD:
            self.registers[register] = value
            self.baud = {code: baud for baud, code in proto.BAUD_CODES.items()}[value]
            return b""  # Reply is lost: the sensor already switched rate.
        self.registers[register] = value
        return frame


class FakeSerial:
    def __init__(self, sensors, baudrate=9600, prefix=b"", drop_next=0):
        self.sensors = sensors
        self.baudrate = baudrate
        self.timeout = None
        self.prefix = prefix
        self.drop_next = drop_next
        self.rx = bytearray()
        self.written = []
        self.closed = False

    def reset_input_buffer(self):
        self.rx.clear()

    def write(self, frame):
        frame = bytes(frame)
        self.written.append(frame)
        if self.drop_next:
            self.drop_next -= 1
            return len(frame)
        for sensor in self.sensors:
            if sensor.baud == self.baudrate:
                reply = sensor.handle(frame)
                if reply:
                    self.rx += self.prefix + reply
                    self.prefix = b""
        return len(frame)

    def read(self, size):
        chunk = bytes(self.rx[:size])
        del self.rx[:size]
        return chunk

    def close(self):
        self.closed = True


def opener_for(serial):
    def opener(port, baud):
        serial.baudrate = baud
        return serial
    return opener
