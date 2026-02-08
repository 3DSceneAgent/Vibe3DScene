from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticRecord:
    request_id: str
    thread_id: str
    session_id: str | None
    process_id: int | None
    log_path: str | None
    elapsed_ms: int
    status: str


def new_request_id(thread_id: str) -> str:
    return f"{thread_id}:{int(time.time() * 1000)}"


def start_timer() -> float:
    return time.monotonic()


def elapsed_ms(start_time: float) -> int:
    return int((time.monotonic() - start_time) * 1000)


def build_diagnostic_record(
    *,
    request_id: str,
    thread_id: str,
    status: str,
    elapsed_ms_value: int,
    session_id: str | None = None,
    process_id: int | None = None,
    log_path: str | None = None,
) -> DiagnosticRecord:
    return DiagnosticRecord(
        request_id=request_id,
        thread_id=thread_id,
        session_id=session_id,
        process_id=process_id,
        log_path=log_path,
        elapsed_ms=elapsed_ms_value,
        status=status,
    )


def within_target(elapsed_ms_value: int, target_ms: int) -> bool:
    return elapsed_ms_value <= target_ms
