from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "check_asset_provider_reachability.py"
    )
    spec = importlib.util.spec_from_file_location("asset_provider_reachability", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_module()


def test_build_sketchfab_checks_without_api_key_skips_auth_probes():
    checks, skipped = module.build_sketchfab_checks(api_key="", model_uid="demo-uid")

    names = [check.name for check in checks]
    skipped_names = [result.name for result in skipped]

    assert names == ["sketchfab-web", "sketchfab-api-search"]
    assert skipped_names == ["sketchfab-api-me", "sketchfab-api-download"]


def test_build_sketchfab_checks_with_api_key_adds_auth_probes():
    checks, skipped = module.build_sketchfab_checks(api_key="secret", model_uid="demo-uid")

    names = [check.name for check in checks]

    assert names == [
        "sketchfab-web",
        "sketchfab-api-search",
        "sketchfab-api-me",
        "sketchfab-api-download",
    ]
    assert skipped == []


def test_summarize_results_counts_pass_fail_and_skip():
    results = [
        module.CheckResult("a", "dns", "a", True, 1.0, "ok"),
        module.CheckResult("b", "tcp", "b", False, 1.0, "failed"),
        module.CheckResult("c", "http", "c", None, 0.0, "skipped"),
    ]

    assert module.summarize_results(results) == {
        "passed": 1,
        "failed": 1,
        "skipped": 1,
        "total": 3,
    }
