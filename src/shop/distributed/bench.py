"""Вимірювання «наївного» і «захищеного» клієнта для ЛР3.

Скрипт піднімає три реальні процеси: платіжний шлюз, ядро з наївним
клієнтом і ядро із захищеним клієнтом. Для кожного режиму збоїв він
вимірює частку успішних команд, p95 затримки та кількість викликів, що
дійшли до залежного сервісу. Результати зберігаються у
`docs/lr3/measurements.json` і `docs/lr3/measurements.md`.

Запуск:
    py -3 -m shop.distributed.bench
    py -3 -m shop.distributed.bench --commands 200 --workers 8
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .jsonutil import (
    JsonObject,
    as_float,
    as_int,
    as_list,
    as_object,
    as_str,
    field,
)


@dataclass
class ServiceProcess:
    """Окремий процес сервісу з логом."""

    name: str
    module: str
    env: dict[str, str]
    log_path: Path
    process: subprocess.Popen[bytes] | None = None
    log_handle: BinaryIO | None = None

    def start(self, root: Path) -> None:
        """Запускає модуль як окремий процес."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("wb")
        environment = os.environ.copy()
        environment.update(self.env)
        environment["PYTHONPATH"] = (
            str(root / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        )
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "-m", self.module],
            cwd=str(root),
            env=environment,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
        )

    def stop(self) -> None:
        """Зупиняє процес і закриває лог."""
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5.0)
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None


@dataclass(frozen=True, slots=True)
class CallRecord:
    """Один виклик команди з тривалістю та відповіддю."""

    duration_ms: float
    status_code: int
    body: JsonObject


@dataclass(frozen=True, slots=True)
class Measurement:
    """Результат одного режиму для одного клієнта."""

    mode: str
    client: str
    commands: int
    success: int
    degraded: int
    rejected: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    calls: int
    attempts: int
    breaker_state: str
    transitions: int
    max_lag_events: int
    max_lag_ms: float
    catch_up_ms: float

    def to_json(self) -> JsonObject:
        """Серіалізує вимірювання."""
        return {
            "mode": self.mode,
            "client": self.client,
            "commands": self.commands,
            "success": self.success,
            "degraded": self.degraded,
            "rejected": self.rejected,
            "successRate": round(self.success / self.commands * 100.0, 1),
            "degradedRate": round(self.degraded / self.commands * 100.0, 1),
            "p50Ms": round(self.p50_ms, 1),
            "p95Ms": round(self.p95_ms, 1),
            "p99Ms": round(self.p99_ms, 1),
            "maxMs": round(self.max_ms, 1),
            "calls": self.calls,
            "attempts": self.attempts,
            "breakerState": self.breaker_state,
            "transitions": self.transitions,
            "maxLagEvents": self.max_lag_events,
            "maxLagMs": round(self.max_lag_ms, 1),
            "catchUpMs": round(self.catch_up_ms, 1),
        }


@dataclass(frozen=True, slots=True)
class BreakerCycleStep:
    """Крок демонстрації Closed → Open → Half-open → Closed."""

    label: str
    state: str
    transitions: list[str]

    def to_json(self) -> JsonObject:
        """Серіалізує крок."""
        return {
            "label": self.label,
            "state": self.state,
            "transitions": self.transitions,
        }


def http_json(
    method: str,
    url: str,
    payload: JsonObject | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, JsonObject]:
    """HTTP-виклик із поверненням статусу й JSON-тіла."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    if headers is not None:
        merged.update(headers)
    request = Request(url, data=data, method=method, headers=merged)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return response.status, {}
            return response.status, as_object(json.loads(raw), "response")
    except HTTPError as error:
        raw = error.read()
        if not raw:
            return error.code, {}
        return error.code, as_object(json.loads(raw), "error")
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"{method} {url}: {error}") from error


def wait_ready(base_url: str, timeout: float = 20.0) -> None:
    """Чекає, доки сервіс відповість на /health."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _payload = http_json(
                "GET", base_url + "/health", timeout=0.5
            )
            if status == 200:
                return
        except RuntimeError:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"сервіс {base_url} не піднявся за {timeout} с")


def free_port() -> int:
    """Вільний TCP-порт на localhost."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def line_payload() -> JsonObject:
    """Одна позиція замовлення для команд."""
    return {
        "sku": "PHN-0001",
        "name": "Смартфон Galaxy X",
        "unitPrice": {"amount": "19999.00", "currency": "UAH"},
        "quantity": 1,
    }


def create_order(core_url: str) -> str:
    """Створює одне замовлення й повертає його id."""
    order_id = str(uuid4())
    body: JsonObject = {
        "commandId": str(uuid4()),
        "type": "PlaceOrder",
        "orderId": order_id,
        "customerId": str(uuid4()),
        "lines": [line_payload()],
    }
    status, payload = http_json("POST", core_url + "/commands", body)
    if status != 200 or payload.get("status") != "applied":
        raise RuntimeError(f"PlaceOrder не вдався: {status} {payload}")
    return order_id


def create_orders(core_url: str, count: int, workers: int) -> list[str]:
    """Створює count замовлень паралельно."""
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda _index: create_order(core_url), range(count)))


def pay_order(core_url: str, order_id: str) -> CallRecord:
    """Одна команда PayOrder із вимірюванням часу."""
    body: JsonObject = {
        "commandId": str(uuid4()),
        "type": "PayOrder",
        "orderId": order_id,
    }
    started = time.perf_counter()
    status, payload = http_json("POST", core_url + "/commands", body)
    duration_ms = (time.perf_counter() - started) * 1000.0
    return CallRecord(duration_ms=duration_ms, status_code=status, body=payload)


def pay_orders(
    core_url: str,
    order_ids: list[str],
    workers: int,
) -> list[CallRecord]:
    """Оплачує замовлення паралельно."""
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda order_id: pay_order(core_url, order_id), order_ids))


def percentile(values: list[float], percent: float) -> float:
    """Перцентиль без зовнішніх бібліотек."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((percent / 100.0) * (len(ordered) - 1))
    return ordered[max(0, min(index, len(ordered) - 1))]


class LagSampler:
    """Фоново читає /projection/lag під час навантаження."""

    def __init__(self, core_url: str) -> None:
        self._core_url = core_url
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._max_events = 0
        self._max_ms = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[int, float]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            return self._max_events, self._max_ms

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                status, payload = http_json(
                    "GET", self._core_url + "/projection/lag", timeout=2.0
                )
                if status == 200:
                    events = as_int(field(payload, "lagEvents"), "lagEvents")
                    lag_ms = as_float(field(payload, "lagMs"), "lagMs")
                    with self._lock:
                        self._max_events = max(self._max_events, events)
                        self._max_ms = max(self._max_ms, lag_ms)
            except (RuntimeError, ValueError):
                pass
            self._stop.wait(0.02)


def wait_projection(core_url: str, timeout: float = 5.0) -> float:
    """Чекає, доки проєкція наздожене журнал; повертає час у мс."""
    started = time.perf_counter()
    deadline = started + timeout
    while time.perf_counter() < deadline:
        status, payload = http_json(
            "GET", core_url + "/projection/lag", timeout=2.0
        )
        if status == 200 and as_int(field(payload, "lagEvents"), "lagEvents") == 0:
            break
        time.sleep(0.01)
    return (time.perf_counter() - started) * 1000.0


def measure_client(
    core_url: str,
    payment_url: str,
    mode: str,
    commands: int,
    workers: int,
) -> Measurement:
    """Один прогін для одного клієнта в одному режимі."""
    http_json("POST", core_url + "/admin/reset")
    order_ids = create_orders(core_url, commands, workers)
    http_json("POST", payment_url + "/_admin/reset")
    status, _state = http_json(
        "POST", payment_url + "/_admin/mode", {"mode": mode}
    )
    if status != 200:
        raise RuntimeError(f"не вдалося перемкнути режим {mode}: {status}")

    sampler = LagSampler(core_url)
    sampler.start()
    records = pay_orders(core_url, order_ids, workers)
    max_lag_events, max_lag_ms = sampler.stop()
    catch_up_ms = wait_projection(core_url)

    status, payment_state_raw = http_json(
        "GET", payment_url + "/_admin/state"
    )
    if status != 200:
        raise RuntimeError("не вдалося прочитати стан платіжного сервісу")
    payment_state = as_object(payment_state_raw, "payment state")
    stats = as_object(field(payment_state, "stats"), "stats")
    calls = as_int(field(stats, "total"), "total")

    status, core_stats_raw = http_json("GET", core_url + "/stats")
    if status != 200:
        raise RuntimeError("не вдалося прочитати стан ядра")
    core_stats = as_object(core_stats_raw, "core stats")
    client_stats = as_object(
        field(core_stats, "paymentClient"), "paymentClient"
    )
    attempts = as_int(field(client_stats, "attempts"), "attempts")
    breaker_state = as_str(field(client_stats, "breakerState"), "breakerState")
    transitions = len(as_list(field(client_stats, "transitions"), "transitions"))

    success = 0
    degraded = 0
    rejected = 0
    durations: list[float] = []
    for record in records:
        durations.append(record.duration_ms)
        if (
            record.body.get("status") == "applied"
            and record.body.get("orderStatus") == "paid"
        ):
            success += 1
        elif record.body.get("status") == "degraded":
            degraded += 1
        else:
            rejected += 1

    config_stats = as_object(field(core_stats, "config"), "config")
    return Measurement(
        mode=mode,
        client=as_str(field(config_stats, "clientMode"), "clientMode"),
        commands=commands,
        success=success,
        degraded=degraded,
        rejected=rejected,
        p50_ms=percentile(durations, 50.0),
        p95_ms=percentile(durations, 95.0),
        p99_ms=percentile(durations, 99.0),
        max_ms=max(durations) if durations else 0.0,
        calls=calls,
        attempts=attempts,
        breaker_state=breaker_state,
        transitions=transitions,
        max_lag_events=max_lag_events,
        max_lag_ms=max_lag_ms,
        catch_up_ms=catch_up_ms,
    )


def _breaker_snapshot(core_url: str) -> tuple[str, list[str]]:
    status, payload = http_json("GET", core_url + "/stats")
    if status != 200:
        raise RuntimeError("не вдалося прочитати /stats")
    client_stats = as_object(field(payload, "paymentClient"), "paymentClient")
    state = as_str(field(client_stats, "breakerState"), "breakerState")
    raw_transitions = as_list(
        field(client_stats, "transitions"), "transitions"
    )
    transitions = [
        f"{as_str(field(item, 'from'), 'from')}->{as_str(field(item, 'to'), 'to')}"
        for item in raw_transitions
        if isinstance(item, dict)
    ]
    return state, transitions


def breaker_cycle(core_url: str, payment_url: str) -> list[BreakerCycleStep]:
    """Демонструє Closed → Open → Half-open → Closed на реальному сервісі."""
    http_json("POST", core_url + "/admin/reset")
    order_ids = create_orders(core_url, 4, 1)
    steps: list[BreakerCycleStep] = []

    http_json("POST", payment_url + "/_admin/mode", {"mode": "unavailable"})
    pay_order(core_url, order_ids[0])
    pay_order(core_url, order_ids[1])
    state, transitions = _breaker_snapshot(core_url)
    steps.append(
        BreakerCycleStep(
            label="1. Після серії збоїв (8 невдалих спроб)",
            state=state,
            transitions=transitions,
        )
    )

    time.sleep(2.2)
    pay_order(core_url, order_ids[2])
    state, transitions = _breaker_snapshot(core_url)
    steps.append(
        BreakerCycleStep(
            label="2. Після паузи: Half-open і невдалий пробний виклик",
            state=state,
            transitions=transitions,
        )
    )

    time.sleep(2.2)
    http_json("POST", payment_url + "/_admin/mode", {"mode": "normal"})
    pay_order(core_url, order_ids[3])
    state, transitions = _breaker_snapshot(core_url)
    steps.append(
        BreakerCycleStep(
            label="3. Після паузи й успішного пробного виклику",
            state=state,
            transitions=transitions,
        )
    )
    return steps


def write_markdown(path: Path, result: JsonObject) -> None:
    """Пише готову таблицю для README."""
    measurements = as_list(field(result, "measurements"), "measurements")
    lines = [
        "| Режим | Клієнт | Успіх, % | Деградовано, % | p50, мс | p95, мс | p99, мс | макс, мс | Викликів | Спроби | CB |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item_raw in measurements:
        item = as_object(item_raw, "measurement")
        lines.append(
            "| {mode} | {client} | {success:.1f} | {degraded:.1f} | {p50:.1f} | {p95:.1f} | {p99:.1f} | {max_ms:.1f} | {calls} | {attempts} | {breaker} |".format(
                mode=as_str(field(item, "mode"), "mode"),
                client=as_str(field(item, "client"), "client"),
                success=as_float(field(item, "successRate"), "successRate"),
                degraded=as_float(field(item, "degradedRate"), "degradedRate"),
                p50=as_float(field(item, "p50Ms"), "p50Ms"),
                p95=as_float(field(item, "p95Ms"), "p95Ms"),
                p99=as_float(field(item, "p99Ms"), "p99Ms"),
                max_ms=as_float(field(item, "maxMs"), "maxMs"),
                calls=as_int(field(item, "calls"), "calls"),
                attempts=as_int(field(item, "attempts"), "attempts"),
                breaker=as_str(field(item, "breakerState"), "breakerState"),
            )
        )
    lines.extend(
        [
            "",
            "### Переходи Circuit Breaker",
            "",
        ]
    )
    for step_raw in as_list(field(result, "breakerCycle"), "breakerCycle"):
        step = as_object(step_raw, "breaker step")
        lines.append(
            f"- **{as_str(field(step, 'label'), 'label')}**: "
            f"`{as_str(field(step, 'state'), 'state')}`; "
            f"{' → '.join(as_str(item, 'transition') for item in as_list(field(step, 'transitions'), 'transitions'))}"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(commands: int, workers: int) -> JsonObject:
    """Піднімає процеси, вимірює всі режими й зберігає результати."""
    root = Path(__file__).resolve().parents[3]
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    work = root / "var" / "lr3" / "bench" / run_id
    logs = work / "logs"
    docs = root / "docs" / "lr3"
    docs.mkdir(parents=True, exist_ok=True)

    payment_port = free_port()
    naive_port = free_port()
    protected_port = free_port()
    payment_url = f"http://127.0.0.1:{payment_port}"
    naive_url = f"http://127.0.0.1:{naive_port}"
    protected_url = f"http://127.0.0.1:{protected_port}"

    common_core_env = {
        "SHOP_PAYMENT_URL": payment_url,
        "SHOP_REQUEST_TIMEOUT": "3.0",
        "SHOP_ATTEMPT_TIMEOUT": "0.25",
        "SHOP_RETRY_ATTEMPTS": "4",
        "SHOP_RETRY_BASE_DELAY": "0.1",
        "SHOP_RETRY_MAX_DELAY": "0.8",
        "SHOP_BREAKER_THRESHOLD": "8",
        "SHOP_BREAKER_OPEN_SECONDS": "2.0",
        "SHOP_BULKHEAD_LIMIT": "8",
        "SHOP_PROJECTION_POLL_SECONDS": "0.01",
        "SHOP_JITTER_SEED": "42",
    }
    naive_env = dict(common_core_env)
    naive_env.update(
        {
            "SHOP_CORE_PORT": str(naive_port),
            "SHOP_PAYMENT_CLIENT": "naive",
            "SHOP_EVENT_LOG": str(work / "naive" / "events.jsonl"),
            "SHOP_CHECKPOINT": str(work / "naive" / "projection.json"),
        }
    )
    protected_env = dict(common_core_env)
    protected_env.update(
        {
            "SHOP_CORE_PORT": str(protected_port),
            "SHOP_PAYMENT_CLIENT": "protected",
            "SHOP_EVENT_LOG": str(work / "protected" / "events.jsonl"),
            "SHOP_CHECKPOINT": str(work / "protected" / "projection.json"),
        }
    )
    payment = ServiceProcess(
        name="payment",
        module="shop.distributed.payment_service",
        env={
            "SHOP_PAYMENT_PORT": str(payment_port),
            "SHOP_PAYMENT_DELAY": "0.08",
            "SHOP_PAYMENT_SLOW_DELAY": "1.0",
            "SHOP_PAYMENT_ERROR_FRACTION": "0.5",
            "SHOP_PAYMENT_SEED": "42",
        },
        log_path=logs / "payment.log",
    )
    naive = ServiceProcess(
        name="naive",
        module="shop.distributed.core_service",
        env=naive_env,
        log_path=logs / "naive.log",
    )
    protected = ServiceProcess(
        name="protected",
        module="shop.distributed.core_service",
        env=protected_env,
        log_path=logs / "protected.log",
    )
    processes = [payment, naive, protected]
    try:
        payment.start(root)
        wait_ready(payment_url)
        naive.start(root)
        protected.start(root)
        wait_ready(naive_url)
        wait_ready(protected_url)

        measurements: list[Measurement] = []
        for mode in ("normal", "slow", "errors", "unavailable"):
            for client_name, client_url in (
                ("naive", naive_url),
                ("protected", protected_url),
            ):
                measurement = measure_client(
                    client_url, payment_url, mode, commands, workers
                )
                measurements.append(measurement)
                print(
                    f"{mode:12s} {client_name:10s} "
                    f"success={measurement.success:3d} "
                    f"degraded={measurement.degraded:3d} "
                    f"p95={measurement.p95_ms:7.1f} мс "
                    f"calls={measurement.calls:4d} "
                    f"cb={measurement.breaker_state}",
                    flush=True,
                )

        print("breaker cycle: closed -> open -> half_open -> closed", flush=True)
        steps = breaker_cycle(protected_url, payment_url)

        result: JsonObject = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "machine": {
                "platform": platform.platform(),
                "python": sys.version,
                "cpuCount": os.cpu_count(),
            },
            "parameters": {
                "commandsPerMode": commands,
                "workers": workers,
                "paymentNormalDelaySeconds": 0.08,
                "paymentSlowDelaySeconds": 1.0,
                "paymentErrorFraction": 0.5,
                "naiveRequestTimeoutSeconds": 3.0,
                "attemptTimeoutSeconds": 0.25,
                "retryAttempts": 4,
                "retryBaseDelaySeconds": 0.1,
                "retryMaxDelaySeconds": 0.8,
                "breakerThreshold": 8,
                "breakerOpenSeconds": 2.0,
                "bulkheadLimit": 8,
            },
            "measurements": [item.to_json() for item in measurements],
            "breakerCycle": [step.to_json() for step in steps],
        }
        (docs / "measurements.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_markdown(docs / "measurements.md", result)
        print(f"результати: {docs / 'measurements.json'}", flush=True)
        return result
    finally:
        for process in processes:
            process.stop()


def main() -> int:
    """Точка входу."""
    parser = argparse.ArgumentParser(description="Вимірювання ЛР3")
    parser.add_argument("--commands", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.commands < 1 or args.workers < 1:
        parser.error("--commands і --workers мають бути ≥ 1")
    run_benchmark(args.commands, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
