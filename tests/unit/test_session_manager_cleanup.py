import subprocess

from scene_agent.blender.session_manager import SessionManager


class DummyProcess:
    def __init__(self, should_timeout: bool = False):
        self.terminated = False
        self.killed = False
        self.should_timeout = should_timeout

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def poll(self):
        return None

    def wait(self, timeout: float | None = None):
        if self.should_timeout:
            raise subprocess.TimeoutExpired(cmd="dummy", timeout=timeout or 0)
        return 0


def test_shutdown_all_terminates_processes():
    manager = SessionManager()
    manager.ensure("session-a", "headless")
    manager.ensure("session-b", "headless")

    process_ok = DummyProcess()
    process_timeout = DummyProcess(should_timeout=True)

    manager.set_process("session-a", process_ok)
    manager.set_mcp_process("session-b", process_timeout)

    manager.shutdown_all()

    assert process_ok.terminated is True
    assert process_ok.killed is False
    assert process_timeout.terminated is True
    assert process_timeout.killed is True
