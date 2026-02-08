import io
import os

from scene_agent.blender.session_manager import BlenderSession, start_headless_process


def test_start_headless_process_redirects_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("BLENDER_HEADLESS_LOG_DIR", str(tmp_path))

    captured = {}

    class DummyPopen:
        def __init__(self, args, stdout=None, stderr=None, **_kwargs):
            captured["args"] = args
            captured["stdout"] = stdout
            captured["stderr"] = stderr
            self._pid = 4321

        def poll(self):
            return None

        @property
        def pid(self):
            return self._pid

    monkeypatch.setattr("scene_agent.blender.session_manager.subprocess.Popen", DummyPopen)

    session = BlenderSession(session_id="session-1", mode="headless")
    start_headless_process(session, "blender", ["--background"])

    assert session.process is not None
    assert session.log_path is not None
    assert session.log_path.startswith(str(tmp_path))
    assert isinstance(captured.get("stdout"), io.IOBase)
    assert isinstance(captured.get("stderr"), io.IOBase)
    assert os.path.basename(session.log_path).startswith("headless_session-1_")
