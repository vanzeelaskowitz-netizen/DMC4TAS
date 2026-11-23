"""Compare real loopback probe/step requests with a deterministic offline controller."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from threading import Thread
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))
from client import Client
from service import Service
from rollback import Rollback
from timeline import parse_script


class Controller:
    build = {"game": "se"}
    session = True
    frame = 0
    score = 0

    def status(self):
        return {"frame": self.frame, "parked": True, "last": None}

    pause = status

    def load(self, timeline, position=0):
        self.keys = [keys for start, end, keys in timeline.rows for _ in range(start, end + 1)]
        self.position = position

    def step(self, count):
        rows = []
        for _ in range(count):
            held = self.keys[self.position] if self.position < len(self.keys) else ()
            self.score += len(held)
            self.position += 1
            self.frame += 1
            rows.append({"number": self.frame, "score": self.score})
        return rows


class Engine:
    live = None

    def __init__(self, ctl):
        self.ctl = ctl

    def capture(self):
        return SimpleNamespace(frame=self.ctl.frame, score=self.ctl.score)

    def restore(self, snapshot):
        self.ctl.frame, self.ctl.score = snapshot.frame, snapshot.score


def compare(server, client, folder):
    ctl = server.ctl
    route = server.route = Rollback(ctl)
    route.engine = Engine(ctl)
    route.timeline = parse_script("7 A\n9 K\n")
    ctl.load(route.timeline)
    route.save(detail=False)
    ctl.step(10)
    initial = folder / "source.tas"
    initial.write_text("7 A\n9 K\n", encoding="utf-8")
    route.path = str(initial)
    server.trace = {2: "original", 15: "future"}
    assert client.call("timeline-keys", first=0, last=0) == [[]]
    assert client.call("timeline-keys", first=7, last=8) == [["A"], ["K"]]
    assert ctl.frame == 10 and ctl.score == 10
    checkpoint = route.checkpoints[0][0]
    trials = [["J"] * 12, ["Space J"] * 24, []]
    args = dict(first=8, last=31, candidates=trials, fields=["frame", "score"])
    start = perf_counter()
    result = client.call("probe", **args)
    probe_ms = (perf_counter() - start) * 1000
    assert (ctl.frame, ctl.score, ctl.position, len(route.checkpoints)) == (10, 10, 10, 1)
    assert server.trace == {2: "original", 15: "future"}
    assert route.path == str(initial) and initial.read_text() == "7 A\n9 K\n"
    assert ctl.keys[-1] == {"K"}
    forward = client.call("probe", **(args | {"first": 130, "last": 130, "candidates": [[]]}))
    assert forward == [[{"frame": 130, "score": 16}]]
    assert len(route.checkpoints) == 1 and route.checkpoints[0][0] is checkpoint
    assert ctl.frame == 10 and server.trace == {2: "original", 15: "future"}
    expected = []
    start = perf_counter()
    for trial in trials:
        client.call("goto", count=7)
        path = folder / "trial.tas"
        path.write_text("\n".join(["7 A", *trial]) + "\n", encoding="utf-8")
        client.call("load", path=str(path))
        rows = [client.call("step", count=1)[0] for _ in range(24)]
        expected.append([{"frame": r["timelineFrame"], "score": r["score"]} for r in rows])
    step_ms = (perf_counter() - start) * 1000
    assert result == expected
    print(f"3 candidates x 24 frames, same results: probe {probe_ms:.2f} ms, step {step_ms:.2f} ms")
    frame, score = ctl.frame, ctl.score
    try:
        server.dispatch("probe", args | {"fields": ["missing"]})
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("invalid field accepted")
    assert (ctl.frame, ctl.score) == (frame, score)
    print("original cursor, input, checkpoints and trace preserved; failed probe restored")


if __name__ == "__main__":
    with Service(Controller()) as server, TemporaryDirectory(dir=ROOT / "logs") as folder:
        info = dict(token=server.token, port=server.server_address[1], game_pid=0)
        with patch("client.read_state", return_value=info):
            client = Client()
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            compare(server, client, Path(folder))
        finally:
            server.shutdown()
            thread.join()
