"""Authenticated JSON client for the local TAS service."""
import json
from pathlib import Path
import socket
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "logs" / "tas-service.json"


def read_state():
    """The service's address file. Windows refuses access while another process is replacing or
    deleting it (the player polls it many times a second), so retry briefly instead of failing."""
    for attempt in range(40):
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise RuntimeError("TAS service is down. Start it with tasctl.py start.") from error
        except (PermissionError, json.JSONDecodeError):
            time.sleep(.025)
    raise RuntimeError("Could not read logs/tas-service.json")


def remove_state():
    """Delete the address file, tolerating a reader that holds it for a moment."""
    for attempt in range(80):
        try:
            STATE.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(.025)
    raise RuntimeError("logs/tas-service.json is locked by another program")


class Client:
    def __init__(self, pid=None):
        self.info = read_state()
        self.pid = self.info["game_pid"]
        if pid is not None and pid != self.pid:
            raise RuntimeError(f"Service owns game PID {self.pid}, not {pid}")

    def call(self, command, **arguments):
        # A restarted service writes a new address file (new port and token). Follow it, so a
        # long-lived client such as the player keeps working (it hung after a restart, 2026-11-25).
        try:
            if STATE.stat().st_mtime_ns != getattr(self, "seen", None):
                info = read_state()
                if info["game_pid"] == self.pid:
                    self.info = info
                self.seen = STATE.stat().st_mtime_ns
        except (OSError, RuntimeError):
            pass
        request = {"token": self.info["token"], "command": command, "args": arguments}
        with socket.create_connection(("127.0.0.1", self.info["port"]), timeout=3) as conn:
            conn.settimeout(600 if command in ("probe", "accept") else 120)
            conn.sendall(json.dumps(request).encode("utf-8") + b"\n")
            with conn.makefile("rb") as response:
                line = response.readline()
        if not line:
            raise RuntimeError("TAS service closed the request without a reply")
        payload = json.loads(line)
        if "error" in payload:
            raise RuntimeError(payload["error"])
        return payload["result"]
