from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

import pytest
from playwright.sync_api import Page

from tests.conftest import make_config

_PORT = 18321


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Server on port {port} did not start in time")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    cfg = make_config(tmp)

    env = {**os.environ, "SYMPHONY_CONFIG": str(cfg), "SYMPHONY_SKIP_DISCOVERY": "1"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "symphony.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(_PORT),
            "--log-level",
            "warning",
        ],
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    _wait_for_port(_PORT)
    yield f"http://127.0.0.1:{_PORT}"

    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def console_page(page: Page, server: str) -> Page:
    page.goto(server)
    page.wait_for_selector(".musician-chip", timeout=10_000)
    return page
