"""
Almemo concurrency stress tests — Part A (automated, no physical device interaction needed).
Target: http://172.18.0.2:3040
"""
import threading
import time
import urllib.request
import urllib.error
import json
import sys

BASE = "http://172.18.0.2:3040"
RESULTS: list[dict] = []
RESULTS_LOCK = threading.Lock()


def get(path, label=""):
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=12) as r:
            data = json.loads(r.read())
            elapsed = time.monotonic() - t0
            return {"ok": True, "label": label, "path": path, "elapsed": elapsed, "data": data}
    except Exception as e:
        elapsed = time.monotonic() - t0
        return {"ok": False, "label": label, "path": path, "elapsed": elapsed, "error": str(e)}


def post(path, body, label=""):
    t0 = time.monotonic()
    try:
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{BASE}{path}", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
            elapsed = time.monotonic() - t0
            return {"ok": True, "label": label, "path": path, "elapsed": elapsed, "data": data}
    except Exception as e:
        elapsed = time.monotonic() - t0
        return {"ok": False, "label": label, "path": path, "elapsed": elapsed, "error": str(e)}


def record(r):
    with RESULTS_LOCK:
        RESULTS.append(r)


def run_threads(fns):
    barrier = threading.Barrier(len(fns))
    threads = []
    for fn in fns:
        def wrapped(f=fn):
            barrier.wait()
            result = f()
            if result is not None:
                record(result)
        t = threading.Thread(target=wrapped, daemon=True)
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)


def print_results(label, rs):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    ok = sum(1 for r in rs if r.get("ok"))
    fail = len(rs) - ok
    for r in rs:
        status = "OK  " if r.get("ok") else "FAIL"
        elapsed = f"{r['elapsed']*1000:.0f}ms"
        err = f"  ← {r.get('error','')}" if not r.get("ok") else ""
        d = r.get("data", {})
        extra = ""
        if isinstance(d, dict):
            if "status" in d:
                extra = f"  status={d['status']}"
            elif "ok" in d:
                extra = f"  ok={d['ok']}"
                if d.get("lines"):
                    extra += f"  lines={len(d['lines'])}"
        print(f"  [{status}] {elapsed:>6}  {r['label']:<30}{extra}{err}")
    print(f"  → {ok}/{len(rs)} OK, {fail} failed")
    return ok, fail


# ────────────────────────────────────────────────────────────
# A1: 5 одновременных GET /health
# ────────────────────────────────────────────────────────────
def test_a1():
    print("\n\n=== A1: 5 simultaneous GET /health (cold, bypasses cache) ===")
    base_count = len(RESULTS)
    fns = [lambda i=i: get("/health?refresh=1", f"health-{i}") for i in range(5)]
    run_threads(fns)
    rs = RESULTS[base_count:]
    return print_results("A1 — concurrent health", rs)


# ────────────────────────────────────────────────────────────
# A2: 10 быстрых /api/command последовательно, 50ms пауза
# ────────────────────────────────────────────────────────────
def test_a2():
    print("\n\n=== A2: 10 rapid /api/command sequential (50ms gap) ===")
    base_count = len(RESULTS)
    for i in range(10):
        r = post("/api/command", {"command": "t0", "read_lines": 1, "timeout_ms": 2000}, f"cmd-{i}")
        record(r)
        time.sleep(0.05)
    rs = RESULTS[base_count:]
    return print_results("A2 — rapid sequential commands", rs)


# ────────────────────────────────────────────────────────────
# A3: /api/command + /health параллельно
# ────────────────────────────────────────────────────────────
def test_a3():
    print("\n\n=== A3: /api/command vs /health in parallel ===")
    base_count = len(RESULTS)

    def cmd_thread():
        for i in range(5):
            record(post("/api/command", {"command": "t0", "read_lines": 1, "timeout_ms": 2000}, f"cmd-{i}"))
            time.sleep(0.1)

    def health_thread():
        for i in range(5):
            record(get("/health?refresh=1", f"health-{i}"))
            time.sleep(0.08)

    barrier = threading.Barrier(2)

    def wrap(fn):
        barrier.wait()
        fn()

    threads = [threading.Thread(target=wrap, args=(f,), daemon=True) for f in [cmd_thread, health_thread]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    rs = RESULTS[base_count:]
    return print_results("A3 — command + health interleaved", rs)


# ────────────────────────────────────────────────────────────
# A4: 3 потока одновременно шлют команды (SESSION_SWITCH_LOCK)
# ────────────────────────────────────────────────────────────
def test_a4():
    print("\n\n=== A4: 3 threads simultaneous commands (SESSION_SWITCH_LOCK contention) ===")
    base_count = len(RESULTS)
    barrier = threading.Barrier(3)

    def worker(tid):
        barrier.wait()
        for i in range(4):
            r = post("/api/command", {"command": "t0", "read_lines": 1, "timeout_ms": 3000}, f"T{tid}-cmd-{i}")
            record(r)

    threads = [threading.Thread(target=worker, args=(tid,), daemon=True) for tid in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    rs = RESULTS[base_count:]
    return print_results("A4 — 3-thread command contention", rs)


# ────────────────────────────────────────────────────────────
# A5: симуляция F5 ×3 — каждый поток: health + command сразу
# ────────────────────────────────────────────────────────────
def test_a5():
    print("\n\n=== A5: F5 x3 simulation — health + command burst per 'tab' ===")
    base_count = len(RESULTS)

    def page_load(tab):
        # Simulate what a fresh page does: health check + load device info
        r1 = get("/health?refresh=1", f"tab{tab}-health")
        record(r1)
        r2 = post("/api/command", {"command": "t0", "read_lines": 1, "timeout_ms": 2000}, f"tab{tab}-cmd")
        record(r2)

    fns = [lambda t=i: page_load(t) for i in range(3)]
    run_threads(fns)
    rs = RESULTS[base_count:]
    return print_results("A5 — F5×3 page reload simulation", rs)


# ────────────────────────────────────────────────────────────
# A6 (bonus): быстрый Sensor Info — 10 команд без паузы
# ────────────────────────────────────────────────────────────
def test_a6():
    print("\n\n=== A6 (bonus): Sensor Info burst — 10 commands, no sleep between ===")
    base_count = len(RESULTS)
    cmds = ["G00", "M00", "P35", "P01", "P11", "P12", "P06", "P07", "P08", "P09"]
    for i, cmd in enumerate(cmds):
        read_lines = 0 if cmd in ("G00", "M00") else 1
        timeout_ms = 300 if cmd in ("G00", "M00") else 1500
        r = post("/api/command",
                 {"command": cmd, "read_lines": read_lines, "timeout_ms": timeout_ms},
                 f"si-{i}-{cmd}")
        record(r)
    rs = RESULTS[base_count:]
    return print_results("A6 — sensor info burst", rs)


# ────────────────────────────────────────────────────────────
# A7: прерывание команды — 2 потока, один занят, второй врывается
# ────────────────────────────────────────────────────────────
def test_a7():
    print("\n\n=== A7: Interrupt simulation — slow command + immediate health probe ===")
    base_count = len(RESULTS)

    results_local = {}

    def slow_cmd():
        # P15 is slow (up to 60 lines, 3× timeout)
        r = get("/api/p15", "slow-p15")
        results_local["slow"] = r
        record(r)

    def fast_health():
        time.sleep(0.05)  # slight delay to ensure slow_cmd starts first
        r = get("/health?refresh=1", "interrupt-health")
        results_local["health"] = r
        record(r)

    barrier = threading.Barrier(2)

    def wrap(fn):
        barrier.wait()
        fn()

    threads = [threading.Thread(target=wrap, args=(f,), daemon=True) for f in [slow_cmd, fast_health]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    rs = RESULTS[base_count:]
    ok, fail = print_results("A7 — interrupt: P15 + health concurrent", rs)

    s = results_local.get("slow", {})
    h = results_local.get("health", {})
    if s and h:
        slow_ms = s.get("elapsed", 0) * 1000
        health_ms = h.get("elapsed", 0) * 1000
        health_reason = h.get("data", {}).get("reason", "")
        print(f"  → P15 finished in {slow_ms:.0f}ms, health in {health_ms:.0f}ms")
        if health_reason:
            print(f"  → health reason: {health_reason}")
    return ok, fail


# ────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*60}")
    print("  ALMEMO Concurrency Stress Tests")
    print(f"  Target: {BASE}")
    print(f"{'═'*60}")

    # Quick ping
    r = get("/health")
    if not r["ok"]:
        print(f"\nERROR: cannot reach {BASE}: {r.get('error')}")
        sys.exit(1)
    print(f"\nDevice: {r['data'].get('status')} | {r['data'].get('version','?')}")

    total_ok = 0
    total_fail = 0

    for test_fn in [test_a1, test_a2, test_a3, test_a4, test_a5, test_a6, test_a7]:
        ok, fail = test_fn()
        total_ok += ok
        total_fail += fail
        time.sleep(0.5)  # brief settle between test groups

    print(f"\n{'═'*60}")
    print(f"  TOTAL: {total_ok}/{total_ok+total_fail} OK  |  {total_fail} FAILED")
    print(f"{'═'*60}\n")
    sys.exit(0 if total_fail == 0 else 1)
