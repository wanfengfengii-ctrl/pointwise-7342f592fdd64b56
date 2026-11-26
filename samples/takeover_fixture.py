import argparse
from http.client import RemoteDisconnected
import json
import signal

from desk.http import DeskRequestHandler, serve_desk
from desk.press_client import HttpPressClient
from press.http import DropReply, running_server
from tests.workshop import Workshop


class _FixtureDeskHandler(DeskRequestHandler):
    def _post(self, db, path, body):
        status, payload = super()._post(db, path, body)
        if 200 <= status < 300 and path in self.server.drop_next_post:
            self.server.drop_next_post.remove(path)
            raise DropReply
        return status, payload


class TakeoverWorkshop(Workshop):
    def __init__(self, *, host="127.0.0.1", port=0):
        self.host = host
        self.port = port
        self.drop_next_post = set()

    def reopen_desk(self):
        self.desk_stack.close()
        self.drop_next_post.clear()
        server = serve_desk(self.root / "desk.sqlite", self.template_path,
                            HttpPressClient(self.addresses), host=self.host, port=self.port)
        server.RequestHandlerClass = _FixtureDeskHandler
        server.drop_next_post = self.drop_next_post
        self.desk_address = self.desk_stack.enter_context(running_server(server))


def expect(response, status):
    actual_status, result = response
    if actual_status != status:
        raise AssertionError(f"expected HTTP {status}, got {actual_status}: {result}")
    return result


def seed_incident(rig):
    revision = expect(rig.desk("/editions", rig.sample["draft"]), 201)
    batch = expect(rig.desk("/batches", {"revision_id": revision["revision_id"],
                                       **rig.sample["batch"]}), 201)
    batch_id = batch["batch_id"]
    batch_path = f"/batches/{batch_id}"
    original = expect(rig.desk(batch_path + "/dispatch", {"press_id": "A", "request_id": "first-dispatch"}), 200)
    run_id = original["run_id"]
    advance_path = f"/runs/{run_id}/advance"
    controls = rig.controls["A"]

    def advance(request_id):
        return rig.press("A", advance_path, {"request_id": request_id})

    assert expect(advance("front-41"), 200)["number"] == 41
    assert expect(advance("back-41"), 200)["number"] == 41
    controls.drop_after_effect.add("eject-41")
    try:
        advance("eject-41")
    except RemoteDisconnected:
        pass
    else:
        raise AssertionError("expected dropped reply after the physical ejection")
    assert rig.saved_advance("A", "eject-41") is None

    controls.hold_before_arrival.add("front-42")
    assert expect(advance("front-42"), 503)["error"] == "device_waiting"
    assert rig.saved_advance("A", "front-42") is None
    assert "front-42" not in controls.arrived

    assert expect(advance("front-43"), 200)["number"] == 43
    assert expect(advance("back-43"), 200)["number"] == 43
    controls.hold_before_effect.add("eject-43")
    assert expect(advance("eject-43"), 503)["error"] == "device_waiting"
    assert rig.saved_advance("A", "eject-43") is None
    assert controls.arrived["eject-43"].number == 43
    assert controls.arrived["eject-43"].stage == "ejected"

    progress = expect(rig.desk(batch_path + "/progress"), 200)
    events = rig.devices["A"].ejection_events(run_id)
    assert progress["actual"] == events and [event["number"] for event in events] == [41]
    assert progress["missing"] == [42, 43] and rig.devices["B"].count_ejections() == 0
    return {"batch": batch, "original": original, "progress": progress,
            "held_before_arrival": "front-42", "held_before_effect": "eject-43"}


def _stop_preview(_signum, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description="Exercise the festival press incident")
    parser.add_argument("mode", choices=("incident", "serve"), nargs="?", default="incident")
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with TakeoverWorkshop(host=args.host, port=args.port) as rig:
        fixture = seed_incident(rig)
        print(json.dumps({"desk": rig.desk_address, "presses": rig.addresses, **fixture},
                         ensure_ascii=False), flush=True)
        if args.mode == "serve":
            signal.signal(signal.SIGTERM, _stop_preview)
            try:
                while True:
                    signal.pause()
            except KeyboardInterrupt:
                return


if __name__ == "__main__":
    main()
