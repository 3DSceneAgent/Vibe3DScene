#!/usr/bin/env python3
"""Health check utilities for integration services."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests

from scene_agent.utils.tool_service_endpoints import (
    get_assetretrieval_base_url,
    get_retrieval_base_url,
    get_sam_http_base_url,
    get_scenesmith_compat_base_url,
    get_shared_tool_service_host,
)


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    label: str
    host_env_key: str
    port_env_key: str
    default_port: int
    path: str
    base_url_env_key: str | None = None
    default_base_url: str | None = None
    expected_fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServiceCheckResult:
    name: str
    label: str
    url: str
    ok: bool
    status_code: int | None = None
    error: str | None = None
    payload: Dict[str, Any] | None = None
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "url": self.url,
            "ok": self.ok,
            "status_code": self.status_code,
            "error": self.error,
            "missing_fields": self.missing_fields,
            "payload": self.payload,
        }


class ServiceHealthChecker:
    """Utility class for checking service availability on a target host."""

    def __init__(self, host_override: str | None = None, timeout: float = 3.0):
        self.host_override = host_override
        self.timeout = timeout
        self._session = requests.Session()
        self._specs: Dict[str, ServiceSpec] = {
            "trellis2": ServiceSpec(
                name="trellis2",
                label="TRELLIS2",
                host_env_key="TRELLIS2_HOST",
                port_env_key="TRELLIS2_PORT",
                default_port=8001,
                path="/",
                expected_fields={"status": "ok"},
            ),
            "objaverse_retrieval": ServiceSpec(
                name="objaverse_retrieval",
                label="Objaverse Retrieval",
                host_env_key="OBJAVERSE_HOST",
                port_env_key="OBJAVERSE_PORT",
                default_port=8002,
                path="/",
                expected_fields={"status": "running"},
            ),
            "scenesmith_hssd": ServiceSpec(
                name="scenesmith_hssd",
                label="SceneSmith HSSD",
                host_env_key="SCENESMITH_COMPAT_HOST",
                port_env_key="SCENESMITH_COMPAT_PORT",
                default_port=8005,
                path="/hssd/healthz",
                expected_fields={"status": "ok", "service": "hssd", "ready": True},
            ),
            "scenesmith_ambientcg": ServiceSpec(
                name="scenesmith_ambientcg",
                label="SceneSmith AmbientCG",
                host_env_key="SCENESMITH_COMPAT_HOST",
                port_env_key="SCENESMITH_COMPAT_PORT",
                default_port=8005,
                path="/ambientcg/healthz",
                expected_fields={"status": "ok", "service": "ambientcg", "ready": True},
            ),
            "pcg_integrator": ServiceSpec(
                name="pcg_integrator",
                label="PCGIntegrator",
                host_env_key="INFINIGEN_HOST",
                port_env_key="INFINIGEN_PORT",
                default_port=8003,
                path="/health",
                expected_fields={"status": "healthy", "service": "PCGIntegrator3D"},
            ),
            "sam_reconstruct": ServiceSpec(
                name="sam_reconstruct",
                label="SAMServer",
                host_env_key="SAM_HOST",
                port_env_key="SAM_PORT",
                default_port=8004,
                path="/healthz",
                expected_fields={"status": "ok", "sam_service": True, "sam3d_service": True},
            ),
        }

    @property
    def service_names(self) -> list[str]:
        return list(self._specs.keys())

    def _resolve_host(self, spec: ServiceSpec) -> str:
        if self.host_override:
            return self.host_override
        configured_host = os.getenv(spec.host_env_key, "").strip() if spec.host_env_key else ""
        if configured_host:
            return configured_host
        return get_shared_tool_service_host() or "localhost"

    def _resolve_port(self, spec: ServiceSpec) -> int:
        if not spec.port_env_key:
            return spec.default_port
        raw_value = os.getenv(spec.port_env_key)
        if not raw_value:
            return spec.default_port
        try:
            return int(raw_value)
        except ValueError:
            return spec.default_port

    def _resolve_url(self, spec: ServiceSpec) -> str:
        if spec.name == "objaverse_retrieval":
            base_url = get_assetretrieval_base_url()
            if self.host_override:
                base_url = self._apply_host_override_to_base_url(base_url)
            return f"{base_url.rstrip('/')}{spec.path}"

        if spec.name == "scenesmith_hssd":
            base_url = get_retrieval_base_url()
            if self.host_override:
                base_url = self._apply_host_override_to_base_url(base_url)
            return f"{base_url.rstrip('/')}{spec.path}"

        if spec.name == "scenesmith_ambientcg":
            base_url = get_scenesmith_compat_base_url()
            if self.host_override:
                base_url = self._apply_host_override_to_base_url(base_url)
            return f"{base_url.rstrip('/')}{spec.path}"

        if spec.name == "sam_reconstruct":
            base_url = get_sam_http_base_url()
            if self.host_override:
                base_url = self._apply_host_override_to_base_url(base_url)
            return f"{base_url.rstrip('/')}{spec.path}"

        if spec.base_url_env_key:
            base_url = (
                os.getenv(spec.base_url_env_key, spec.default_base_url or "").strip()
                or spec.default_base_url
                or ""
            )
            if base_url:
                effective_base_url = base_url
                if self.host_override:
                    effective_base_url = self._apply_host_override_to_base_url(base_url)
                return f"{effective_base_url.rstrip('/')}{spec.path}"
        host = self._resolve_host(spec)
        port = self._resolve_port(spec)
        return f"http://{host}:{port}{spec.path}"

    def _apply_host_override_to_base_url(self, base_url: str) -> str:
        parsed = urlsplit(base_url)
        if not parsed.scheme or not parsed.netloc:
            return base_url

        hostname = parsed.hostname
        if not hostname:
            return base_url

        new_netloc = self.host_override or hostname
        if parsed.port is not None:
            new_netloc = f"{new_netloc}:{parsed.port}"

        updated = SplitResult(
            scheme=parsed.scheme,
            netloc=new_netloc,
            path=parsed.path,
            query=parsed.query,
            fragment=parsed.fragment,
        )
        return urlunsplit(updated)

    def _check_expected_fields(
        self, payload: Dict[str, Any], expected_fields: Dict[str, Any]
    ) -> list[str]:
        missing: list[str] = []
        for key, expected_value in expected_fields.items():
            if payload.get(key) != expected_value:
                missing.append(f"{key}={expected_value!r}")
        return missing

    def _check_sam_reconstruct_health(self, payload: Dict[str, Any]) -> list[str]:
        status_missing = self._check_expected_fields(payload, {"status": "ok"})
        if status_missing:
            return status_missing

        if (
            payload.get("runtime_mode") == "cache"
            and payload.get("internal_services_expected") is False
        ):
            return []

        return self._check_expected_fields(
            payload,
            {"status": "ok", "sam_service": True, "sam3d_service": True},
        )

    def check_service(self, service_name: str) -> ServiceCheckResult:
        if service_name not in self._specs:
            raise ValueError(
                f"Unknown service '{service_name}'. Valid options: {', '.join(self.service_names)}"
            )

        spec = self._specs[service_name]
        url = self._resolve_url(spec)

        try:
            response = self._session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            return ServiceCheckResult(
                name=spec.name,
                label=spec.label,
                url=url,
                ok=False,
                error=str(exc),
            )

        try:
            payload = response.json()
        except ValueError:
            return ServiceCheckResult(
                name=spec.name,
                label=spec.label,
                url=url,
                ok=False,
                status_code=response.status_code,
                error="response is not valid JSON",
            )

        if spec.name == "sam_reconstruct":
            missing_fields = self._check_sam_reconstruct_health(payload)
        else:
            missing_fields = self._check_expected_fields(payload, spec.expected_fields)
        ok = response.status_code == 200 and not missing_fields

        return ServiceCheckResult(
            name=spec.name,
            label=spec.label,
            url=url,
            ok=ok,
            status_code=response.status_code,
            payload=payload,
            missing_fields=missing_fields,
            error=None if ok else "health contract not satisfied",
        )

    def check_services(
        self, service_names: Iterable[str] | None = None
    ) -> Dict[str, ServiceCheckResult]:
        names = list(service_names) if service_names is not None else self.service_names
        return {name: self.check_service(name) for name in names}


def _parse_services_arg(raw_services: str | None, checker: ServiceHealthChecker) -> list[str]:
    if not raw_services:
        return checker.service_names

    parsed = [item.strip() for item in raw_services.split(",") if item.strip()]
    unknown = [name for name in parsed if name not in checker.service_names]
    if unknown:
        raise ValueError(
            f"Unknown services: {', '.join(unknown)}. Valid options: {', '.join(checker.service_names)}"
        )
    return parsed


def _print_human_readable(results: Dict[str, ServiceCheckResult]) -> None:
    for result in results.values():
        if result.ok:
            print(f"{result.label}: OK {result.url}")
            continue

        detail = result.error or "unknown error"
        if result.missing_fields:
            detail = f"missing expected fields: {', '.join(result.missing_fields)}"
        print(f"{result.label}: FAIL ({detail}) {result.url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Health check for 3DSceneAgent services.")
    parser.add_argument(
        "--host",
        default=None,
        help="Override host/IP for all services (default: use per-service env host).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="HTTP request timeout in seconds (default: 3.0).",
    )
    parser.add_argument(
        "--services",
        default=None,
        help=(
            "Comma-separated service names "
            "(trellis2,objaverse_retrieval,scenesmith_hssd,scenesmith_ambientcg,pcg_integrator,sam_reconstruct)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON results instead of human-readable lines.",
    )
    args = parser.parse_args()

    checker = ServiceHealthChecker(host_override=args.host, timeout=args.timeout)
    try:
        selected_services = _parse_services_arg(args.services, checker)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    results = checker.check_services(selected_services)
    if args.json:
        print(json.dumps({name: result.to_dict() for name, result in results.items()}, ensure_ascii=False))
    else:
        _print_human_readable(results)

    return 0 if all(result.ok for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
