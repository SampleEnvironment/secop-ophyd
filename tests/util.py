"""Shared test helpers: running frappy SEC nodes and importing generated code."""

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path


def import_generated(path: Path, module_name: str):
    """Import a generated node module from ``path`` under a unique name."""
    unique_name = f"_secop_gen_{module_name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        unique_name, path / f"{module_name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STARTUP_PATTERN = "startup done with interface"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def _frappy_server_executable() -> str:
    exe = Path(sys.executable).parent / "frappy-server"
    if exe.exists():
        return str(exe)
    found = shutil.which("frappy-server")
    if found is None:
        raise RuntimeError("frappy-server executable not found")
    return found


class SecNode:
    """A frappy SEC node running in a subprocess on a free port.

    Each pytest(-xdist) worker gets its own ports, so nodes never collide when
    tests run in parallel.
    """

    def __init__(self, cfg: str, name: str, workdir: Path):
        self.cfg = cfg
        self.name = name
        self.port = _free_port()
        self.workdir = workdir
        self.proc: subprocess.Popen | None = None
        self.env = os.environ.copy()
        self.env.update(
            {
                "FRAPPY_CONFDIR": str(PROJECT_ROOT / "cfg"),
                "FRAPPY_LOGDIR": str(workdir / f"logs-{self.port}"),
                "FRAPPY_PIDDIR": str(workdir / f"pid-{self.port}"),
                "FRAPPY_PROJECT_ROOT": str(PROJECT_ROOT),
            }
        )

    @property
    def uri(self) -> str:
        return f"localhost:{self.port}"

    def start(self, timeout: float = 15) -> "SecNode":
        log_path = self.workdir / f"{self.name}-{self.port}.log"
        log_file = log_path.open("ab")
        offset = log_path.stat().st_size
        self.proc = subprocess.Popen(
            [
                _frappy_server_executable(),
                "-c",
                self.cfg,
                "-p",
                str(self.port),
                self.name,
            ],
            env=self.env,
            cwd=self.workdir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with log_path.open("rb") as f:
                f.seek(offset)
                if STARTUP_PATTERN in f.read().decode(errors="replace"):
                    return self
            if self.proc.poll() is not None:
                break
            time.sleep(0.02)

        self.stop()
        raise RuntimeError(
            f"SEC node {self.name!r} did not start, see {log_path}:\n"
            + log_path.read_text(errors="replace")[-2000:]
        )

    def stop(self) -> None:
        if self.proc is None:
            return
        # the test nodes hold no state worth a graceful shutdown, and frappy
        # takes about a second to stop on SIGTERM
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()
