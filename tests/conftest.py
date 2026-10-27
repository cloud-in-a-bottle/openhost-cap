import subprocess
import threading
import time
from collections.abc import Iterator

import pytest
from openhost_test_harness import OpenhostStack

# TEMPORARY CI diagnostics: the harness removes the container when deploy fails, so snapshot its logs meanwhile.
_logs: list[str] = [""]


def _tail(name: str) -> None:
    while True:
        r = subprocess.run(["podman", "logs", "-t", name], capture_output=True, text=True)
        if r.stdout or r.stderr:
            _logs[0] = r.stdout + r.stderr
        r2 = subprocess.run(["podman", "inspect", name, "--format", "{{.State.Status}} {{.State.ExitCode}} {{.State.StartedAt}}"], capture_output=True, text=True)
        _logs[0] += "\nSTATE: " + r2.stdout + r2.stderr
        time.sleep(2)


def _start_tail(s: OpenhostStack) -> None:
    threading.Thread(target=_tail, args=(s.container_name,), daemon=True).start()


@pytest.fixture(scope="session")
def stack() -> Iterator[OpenhostStack]:
    """Builds the Dockerfile and deploys the app through the real router; returns once the app is running."""
    try:
        with OpenhostStack(deploy_timeout=1800, pre_deploy=_start_tail) as s:
            yield s
    except BaseException:
        print("APP LOGS:\n" + _logs[0][-20000:])
        raise
