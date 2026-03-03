from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_validate_prompt_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "validate_prompt.py"
    spec = spec_from_file_location("validate_prompt_script", module_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_prompt_parser_supports_topology_and_memory_flags():
    module = _load_validate_prompt_module()
    parser = module._build_parser()
    args = parser.parse_args(["--prompt", "hello world"])
    assert args.topology_run == "auto"
    assert args.memory_profile == "auto"


def test_validate_prompt_topology_mapping():
    module = _load_validate_prompt_module()
    assert module._requested_topology_from_run("single") == "single_agent"
    assert module._requested_topology_from_run("dual") == "dual_agent"
    assert module._requested_topology_from_run("auto") == "auto"


def test_validate_prompt_build_compare_summary():
    module = _load_validate_prompt_module()
    summary = module._build_compare_summary(
        {
            "effective_topology": "single_agent",
            "effective_task_mode": "plan_mode",
            "elapsed_sec": 10.4,
            "graph_steps": 14,
            "topology_mismatch": False,
        },
        {
            "effective_topology": "dual_agent",
            "effective_task_mode": "plan_mode",
            "elapsed_sec": 13.1,
            "graph_steps": 19,
            "topology_mismatch": False,
        },
    )
    assert summary["single_effective_topology"] == "single_agent"
    assert summary["dual_effective_topology"] == "dual_agent"
    assert summary["elapsed_delta_sec"] == 2.7
    assert summary["single_graph_steps"] == 14
    assert summary["dual_graph_steps"] == 19
