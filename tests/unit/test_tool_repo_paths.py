from pathlib import Path

from scene_agent.utils import tool_repo_paths


def test_get_agent_tools_root_prefers_env(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "Vibe3DScene"
    repo_root.mkdir()
    configured_root = tmp_path / "custom-tools"
    monkeypatch.setattr(tool_repo_paths, "REPO_ROOT", repo_root)
    monkeypatch.setenv("AGENT_TOOLS_ROOT", str(configured_root))

    assert tool_repo_paths.get_agent_tools_root() == configured_root.resolve()


def test_get_agent_tools_root_defaults_to_sibling_checkout(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "Vibe3DScene"
    repo_root.mkdir()
    monkeypatch.setattr(tool_repo_paths, "REPO_ROOT", repo_root)
    monkeypatch.delenv("AGENT_TOOLS_ROOT", raising=False)

    assert tool_repo_paths.get_agent_tools_root() == (tmp_path / "3DAgentTools").resolve()


def test_get_agent_tools_glb_import_script_uses_tools_checkout(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "Vibe3DScene"
    repo_root.mkdir()
    monkeypatch.setattr(tool_repo_paths, "REPO_ROOT", repo_root)
    monkeypatch.setenv("AGENT_TOOLS_ROOT", str(tmp_path / "tools-root"))

    assert tool_repo_paths.get_agent_tools_glb_import_script() == (
        tmp_path / "tools-root" / "scripts" / "blender" / "glb_import.py"
    ).resolve()
