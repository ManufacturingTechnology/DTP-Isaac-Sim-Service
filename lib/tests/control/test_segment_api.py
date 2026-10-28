"""The segment API's process data: command frames, response frames, the queue, execute, the stream,
generated clients. (The acknowledgement, halt, configuration, and retransmission tests are in
test_segment_protocol.py.)"""
import importlib.util
import sys
import threading
import time

import pytest
import requests

from amt.dtp.ise.lib.control.core.client import CoreApiError
from amt.dtp.ise.lib.control.segment.client import SchemaMismatch, SegmentClient, download_client_module
from amt.dtp.ise.lib.control.segment.server import SegmentApi
from amt.dtp.ise.lib.control.transducer.models import ColorModel, RevoluteJointPositionModel, VelocityModel

from .conftest import FakeCell, free_port


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def load_generated_client(api, tmp_path, name):
    path = download_client_module(f"http://127.0.0.1:{api.port}", tmp_path, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(running_server):
    api, stepper = running_server
    return SegmentClient(f"http://127.0.0.1:{api.port}", api.component_commands_model, api.component_states_model)


def test_read_before_any_step_is_503(running_server, client):
    with pytest.raises(CoreApiError) as info:
        client.read()
    assert info.value.status_code == 503


def test_frames_apply_to_all_components_on_the_same_step(running_server, client, cell, line):
    api, stepper = running_server
    frames = [
        {"frame_id": 1, "components": {"mill": {"conveyor": {"velocity": 0.1}}, "line": {"belt": {"velocity": 1.0}}}},
        {"frame_id": 2, "components": {"line": {"belt": {"velocity": 2.0}}}},
        {"frame_id": 3, "components": {"mill": {"mill": {"A1": {"position": 45}}}}},
    ]
    receipt = client.write(frames)
    assert receipt.accepted == 3 and receipt.queue_length == 3
    assert type(receipt).__name__ == "CommandResponseBase" and receipt.halted is False  # untyped client: the generic base
    assert [f.frame_id for f in api.pending()] == [1, 2, 3]

    first = api.step(0.01, timeline_time=1.0)
    # Both machines changed on the same step.
    assert cell.conveyor == 0.1 and line.belt == 1.0
    assert first.components.mill.commands.conveyor.velocity == 0.1
    assert first.components.line.commands.belt.velocity == 1.0
    second = api.step(0.01, timeline_time=1.0)  # a second physics step in the same app update
    assert cell.conveyor == 0.1 and line.belt == 2.0  # 'mill' omitted: unchanged
    third = api.step(0.01, timeline_time=1.05)
    assert cell.a1 == 45 and line.belt == 2.0
    assert [r.frame_id for r in (first, second, third)] == [1, 2, 3]
    # Both machines were addressed by frame 1; frame 2 only addressed the line.
    assert (first.components.mill.status, first.components.line.status) == ("applied", "applied")
    assert (second.components.mill.status, second.components.line.status) == ("not_addressed", "applied")
    assert [r.physics_timestamp for r in (first, second, third)] == pytest.approx([0.01, 0.02, 0.03])
    assert [r.timeline_timestamp for r in (first, second, third)] == [1.0, 1.0, 1.05]
    assert abs(third.wall_timestamp - time.time()) < 5 and first.wall_timestamp <= third.wall_timestamp
    # Within a component, omitted actuators are unchanged too.
    assert cell.writes[-1].model_fields_set == {"mill"}

    idle = api.step(0.01, timeline_time=1.05)
    assert idle.frame_id is None
    assert {idle.components.mill.status, idle.components.line.status} == {"not_addressed"}
    state = client.read()
    assert state.physics_timestamp == pytest.approx(0.04) and state.timeline_timestamp == 1.05
    assert state.components.mill.commands.mill.A1.position == 45
    assert state.components.mill.actuals.mill.Vise.forces == (0, 0, 1)
    assert state.components.line.actuals.probe.distance == 0.5


def test_write_accepts_single_frames_and_row_models(running_server, client, cell, line):
    api, _ = running_server
    row = api.component_commands_model
    client.write(client.command_frame_model(components=row(line=line.command_model(belt=VelocityModel(velocity=3.0)))))
    client.write(row(mill=cell.command_model(beacon=ColorModel(r=1, g=0, b=0))))
    api.step(0.1)
    api.step(0.2)
    assert line.belt == 3.0 and cell.beacon == ColorModel(r=1, g=0, b=0)


def test_invalid_batch_is_rejected_whole_with_frame_index(running_server, client):
    batch = [
        {"components": {"line": {"belt": {"velocity": 0.1}}}},
        {"components": {"mill": {"mill": {"A1": {"position": 500}}}}},  # outside the joint limit
    ]
    # The client validates before sending...
    with pytest.raises(ValueError, match="less than or equal to 90"):
        client.write(batch)
    # ...and the server rejects the whole batch, naming the frame index.
    api, _ = running_server
    response = requests.post(f"http://127.0.0.1:{api.port}/api/segment/process/write", json=batch)
    assert response.status_code == 422
    locations = [error["loc"] for error in response.json()["detail"]]
    assert ["body", 1, "components", "mill", "mill", "A1", "position"] in locations
    assert api.pending() == []


@pytest.mark.parametrize("frame, location", [
    ({"components": {"nope": {}}}, ["components", "nope"]),                                 # unknown component
    ({"components": {"mill": {"mil": {}}}}, ["components", "mill", "mil"]),                 # unknown actuator
    ({"components": {"mill": {"mill": {"a1": {"position": 1}}}}}, ["components", "mill", "mill", "a1"]),  # joint
    ({"components": {"mill": {"mill": {"A1": {"position": 1, "target_position": 2}}}}},     # unknown field
     ["components", "mill", "mill", "A1", "target_position"]),
    ({"componnts": {}}, ["componnts"]),                                                      # misspelt frame field
])
def test_unknown_names_reject_the_batch(running_server, frame, location):
    # A misspelt name must not be silently ignored: with every level optional, the frame would
    # otherwise be a valid no-op (a delay) and the intended command would never be applied.
    api, _ = running_server
    response = requests.post(f"http://127.0.0.1:{api.port}/api/segment/process/write",
                             json=[{"components": {}}, frame])
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert ["body", 1, *location] in [e["loc"] for e in errors]
    assert all(e["type"] == "extra_forbidden" for e in errors)
    assert api.pending() == []


def test_empty_frames_are_delays(running_server, client):
    api, _ = running_server
    client.write([
        {"frame_id": 0, "components": {"line": {"belt": {"velocity": 0.5}}}},
        {"frame_id": 1},                                    # no components
        {"frame_id": 2, "components": {}},                  # empty components
        {"frame_id": 3, "components": {"line": None}},      # null component
        {"frame_id": 4, "components": {"line": {"belt": {"velocity": 0.0}}}},
    ])
    responses = [api.step(0.01) for _ in range(5)]
    # One frame per step; the delay frames change nothing and report no error.
    assert [r.frame_id for r in responses] == [0, 1, 2, 3, 4]
    assert [r.components.line.commands.belt.velocity for r in responses] == [0.5, 0.5, 0.5, 0.5, 0.0]
    assert all(r.components.line.errors == [] for r in responses)
    assert [r.components.line.status for r in responses] == ["applied", "not_addressed", "not_addressed",
                                                             "not_addressed", "applied"]


def test_queue_limit_is_413(running_server, client):
    client.write([{"components": {}}] * 50)
    with pytest.raises(CoreApiError) as info:
        client.write({"components": {}})
    assert info.value.status_code == 413
    assert "maximum queue length of 50" in str(info.value)


def test_clear_returns_pending_frames(running_server, client):
    api, _ = running_server
    client.write([{"frame_id": i, "components": {}} for i in range(5)])
    removed = client.clear()
    assert [f.frame_id for f in removed] == [0, 1, 2, 3, 4]
    assert api.pending() == []


def test_route_layout(running_server):
    # Process data lives under /api/segment/process, the core under /api/core.
    api, _ = running_server
    root = f"http://127.0.0.1:{api.port}/api"
    assert requests.get(f"{root}/segment/read").status_code == 404
    assert requests.delete(f"{root}/segment/process/clear").status_code == 200
    assert requests.get(f"{root}/core/info").status_code == 200


def test_write_error_is_per_component_and_halts(running_server, client, cell, line):
    api, _ = running_server
    client.write([
        {"frame_id": 1, "components": {"mill": {"conveyor": {"velocity": 0.3}}, "line": {"belt": {"velocity": 9.0}}}},
        {"frame_id": 2, "components": {"line": {"belt": {"velocity": 4.0}}}},
    ])
    line.fail_on_write = True
    failed = api.step(0.1)
    assert failed.frame_id == 1 and failed.halted
    assert failed.components.line.status == "failed"
    [error] = failed.components.line.errors
    assert (error.code, error.message, error.component, error.transducer, error.sequence) == (
        "WRITE_FAILED", "RuntimeError: belt jammed", "line", None, failed.sequence)
    assert failed.components.mill.status == "applied" and cell.conveyor == 0.3  # the other machine still moved
    line.fail_on_write = False
    held = api.step(0.2)  # halted: frame 2 stays queued
    assert held.frame_id is None and held.halted and [f.frame_id for f in api.pending()] == [2]
    client.resume()
    ok = api.step(0.3)
    assert ok.frame_id == 2 and not ok.halted and ok.components.line.status == "applied" and line.belt == 4.0


def test_read_error_is_per_component_and_does_not_halt(running_server, client, cell, line):
    api, _ = running_server
    line.fail_on_read = True
    client.write({"frame_id": 1, "components": {"mill": {"conveyor": {"velocity": 0.4}}}})
    response = api.step(0.1)
    assert response.components.line.actuals is None and response.components.line.commands is None
    [error] = response.components.line.errors
    assert (error.code, error.message) == ("READ_FAILED", "reading failed: RuntimeError: probe offline")
    assert response.components.line.status == "not_addressed" and not response.halted
    assert response.components.mill.errors == [] and response.components.mill.commands.conveyor.velocity == 0.4
    assert client.read().components.line.errors[0].message.startswith("reading failed")
    line.fail_on_read = False
    assert api.step(0.2).components.line.actuals.probe.distance == 0.5


def test_execute_waits_for_its_frame(running_server, client):
    api, stepper = running_server
    client.write([{"frame_id": 1, "components": {}}, {"frame_id": 2, "components": {}}])
    stepper.start()
    response = client.execute({"frame_id": 99, "components": {"line": {"belt": {"velocity": 0.5}}}})
    assert response.frame_id == 99
    assert response.components.line.commands.belt.velocity == 0.5


def test_execute_timeout_removes_the_frame(running_server, client):
    api, _ = running_server
    with pytest.raises(CoreApiError) as info:
        client.execute({"frame_id": 7, "components": {}}, timeout=0.2)
    assert info.value.status_code == 504
    assert "removed from the queue" in str(info.value)
    assert api.pending() == []


def test_clear_fails_waiting_execute_with_409(running_server, client):
    api, _ = running_server
    result = {}

    def run():
        try:
            client.execute({"frame_id": 1, "components": {}}, timeout=5)
        except CoreApiError as ex:
            result["status"] = ex.status_code

    thread = threading.Thread(target=run)
    thread.start()
    wait_for(lambda: len(api.pending()) == 1)
    client.clear()
    thread.join(5)
    assert result["status"] == 409


def test_stream_receives_every_step_in_order(running_server, client):
    api, stepper = running_server
    api.step(1.0)
    with client.stream() as stream:
        iterator = iter(stream)
        first = next(iterator)  # the latest response frame is sent on connect
        for i in range(5):
            api.step(1.0)
        responses = [next(iterator) for _ in range(5)]
    assert [first.physics_timestamp] + [r.physics_timestamp for r in responses] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert responses[0].components.line.actuals.probe.distance == 0.5


def test_stream_filters(running_server, client):
    api, stepper = running_server
    with client.stream(executed_only=True) as stream:
        iterator = iter(stream)
        time.sleep(0.2)  # let the subscription register
        api.step(0.1)  # idle: filtered out
        client.write({"frame_id": 5, "components": {}})
        api.step(0.2)
        assert next(iterator).frame_id == 5
    with client.stream(every=3) as stream:
        iterator = iter(stream)
        next(iterator)  # the latest response frame is always sent on connect
        for i in range(9):
            api.step(1.0)
        received = [next(iterator).physics_timestamp for _ in range(3)]
    assert [received[1] - received[0], received[2] - received[1]] == pytest.approx([3, 3])


def test_slow_stream_subscriber_overflows(running_server):
    # A subscriber only backs up when its TCP connection stops accepting data, which is hard to
    # provoke reliably in a test, so the overflow logic is tested directly.
    from amt.dtp.ise.lib.control.segment.engine import SegmentEvent
    from amt.dtp.ise.lib.control.segment.server import _Subscriber

    api, _ = running_server
    frame = api.step(0.1)
    subscriber = _Subscriber(every=1, executed_only=False, queue_size=2)
    events = [SegmentEvent("frame", sequence, frame, executed=True) for sequence in range(1, 6)]
    for event in events:
        subscriber.offer(event)
    items = [subscriber.queue.get_nowait() for _ in range(subscriber.queue.qsize())]
    assert items[:2] == events[:2]
    assert items[2] is _Subscriber._OVERFLOW and len(items) == 3
    assert subscriber.done


def test_stream_parser_handles_chunking_and_error_events(running_server):
    from amt.dtp.ise.lib.control.segment.client import SegmentStream, SegmentStreamError
    api, _ = running_server
    frame = api.step(0.1)
    text = (
        ": a comment\n\n"
        f"id: {frame.instance_id}:{frame.sequence}\nevent: frame\ndata: {frame.model_dump_json()}\n\n"
        f'event: heartbeat\ndata: {{"instance_id": "{frame.instance_id}", "next_sequence": 9}}\n\n'
        'event: error\ndata: {"errors": [{"code": "STREAM_OVERFLOW", "message": "fell behind"}]}\n\n'
    )

    class FakeResponse:
        url = "http://x/api/segment/stream"
        raw = None

        def iter_content(self, chunk_size=None, decode_unicode=False):
            for i in range(0, len(text), 5):  # split mid-line
                yield text[i:i + 5]

        def close(self):
            pass

    stream = SegmentStream(FakeResponse(), api.response_frame_model)
    iterator = iter(stream)
    assert next(iterator).physics_timestamp == frame.physics_timestamp
    assert (stream.instance_id, stream.last_sequence) == (frame.instance_id, frame.sequence)
    with pytest.raises(SegmentStreamError, match="STREAM_OVERFLOW: fell behind") as info:
        next(iterator)
    assert stream.next_sequence == 9  # from the heartbeat
    assert info.value.code == "STREAM_OVERFLOW" and info.value.errors[0].code == "STREAM_OVERFLOW"


def test_segment_info(running_server, client):
    api, _ = running_server
    info = client.segment_info()
    assert info.title == "Machining Cell" and info.fingerprint == api.fingerprint
    assert [(c.id, c.name, c.version, c.configurable) for c in info.components] == [
        ("mill", "Mill Cell", "0.1.0", False), ("line", "Conveyor Line", "1.2.0", True)]
    assert info.components[0].actuators == ["mill", "conveyor", "beacon"]
    assert info.components[0].sensors == ["mill", "probe"]
    assert client.segment_info().steps == 0
    for timeline_time in (1.0, 1.0, 1.05):  # two physics steps in one app update, then the next update
        api.step(0.0166, timeline_time=timeline_time)
    info = client.segment_info()
    assert info.physics_time_step == pytest.approx(0.0166) and info.steps == 3
    assert info.timeline_time_step == pytest.approx(0.05)
    assert info.halted is False and info.halt is None and info.config_revision == 0
    assert (info.buffer.instance_id, info.buffer.buffer_size) == (api.segment.instance_id, 20)
    assert (info.buffer.first_sequence, info.buffer.last_sequence, info.buffer.next_sequence) == (1, 3, 4)
    # The CoreApi endpoints are there too.
    base = client.info()
    assert base.layers == ["CoreApi", "SegmentApi"]


def test_exact_physics_time_overrides_the_sum(running_server):
    api, _ = running_server
    assert api.step(0.01).physics_timestamp == pytest.approx(0.01)
    assert api.step(0.01, physics_time=5.0).physics_timestamp == 5.0
    assert api.step(0.01).physics_timestamp == pytest.approx(5.01)  # continues from the exact time


def test_generated_client_end_to_end(running_server, tmp_path):
    api, stepper = running_server
    module = load_generated_client(api, tmp_path, "machining_cell_e2e")
    client = module.MachiningCellClient(f"http://127.0.0.1:{api.port}")
    stepper.start()
    response = client.execute(module.CommandFrame(
        frame_id=1,
        components=module.ComponentCommands(
            mill=module.MillCellCommands(mill=module.mill_Commands(A1=RevoluteJointPositionModel(position=30))),
            line=module.ConveyorLineCommands(belt=VelocityModel(velocity=0.7)),
        ),
    ))
    assert isinstance(response, module.MachiningCellResponseFrame) and isinstance(response, module.ResponseFrame)
    assert isinstance(response.components.mill.commands.mill.A1, module.mill_A1_Commands)
    assert response.components.mill.commands.mill.A1.position == 30
    assert response.components.mill.status == "applied" and response.components.mill.errors == []
    assert response.components.line.commands.belt.velocity == 0.7


def test_fingerprint_mismatch_is_detected(running_server, tmp_path):
    api, _ = running_server
    module = load_generated_client(api, tmp_path, "machining_cell_stale")
    module.MachiningCellClient.SCHEMA_FINGERPRINT = "sha256:stale"
    with pytest.raises(SchemaMismatch, match="version was not bumped"):
        module.MachiningCellClient(f"http://127.0.0.1:{api.port}")
    with pytest.warns(UserWarning):
        module.MachiningCellClient(f"http://127.0.0.1:{api.port}", strict=False)


def test_models_py_headers(running_server):
    api, _ = running_server
    response = requests.get(f"http://127.0.0.1:{api.port}/api/segment/models.py")
    assert response.headers["content-disposition"] == 'attachment; filename="machining_cell.py"'
    assert response.headers["etag"] == f'"{api.fingerprint}"'
    header = response.text.splitlines()[:4]
    assert any(line.startswith("# Generated by AMT DTP Isaac Sim Python Library (amt-dtp-ise-lib) ")
               and line.endswith(", part of the AMT DTP Isaac Sim Service (ISS).") for line in header), header


def test_generator_names_the_embedding_application():
    from amt.dtp.ise.lib.control import codegen
    saved = codegen._generator
    try:
        codegen.set_generator("AMT DTP Isaac Sim Extension (ISE)", "1.2.3")
        assert codegen.generator() == "AMT DTP Isaac Sim Extension (ISE) 1.2.3"
    finally:
        codegen._generator = saved


def test_openapi_documents_the_typed_models(running_server):
    api, _ = running_server
    spec = requests.get(f"http://127.0.0.1:{api.port}/api/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ["MachiningCellComponentCommands", "MachiningCellComponentStates", "MachiningCellCommandFrame",
                 "MachiningCellResponseFrame", "MachiningCellCommandResponse", "MachiningCellComponentConfigs",
                 "MachiningCellConfiguration", "MillCellState", "ConveyorLineState", "mill_A1_Commands",
                 "RevoluteJointSensorModel", "AssetInfo", "CoreInfo", "SegmentInfo", "SegmentError", "ConfigEvent",
                 "RetransmitResponse", "SegmentStatus"]:
        assert name in schemas, name
    a1 = schemas["mill_A1_Commands"]["properties"]["position"]
    assert a1["maximum"] == 90 and a1["description"] == "A1 target position in degrees"
    assert spec["info"]["title"] == "Machining Cell"


def test_two_identical_machines(tmp_path):
    a, b = FakeCell(), FakeCell()
    api = SegmentApi({"mill1": a, "mill2": b}, title="Two Mills", port=free_port(), host="127.0.0.1")
    api.start()
    try:
        api.step(0.0)
        module = load_generated_client(api, tmp_path, "two_mills_e2e")
        client = module.TwoMillsClient(f"http://127.0.0.1:{api.port}")
        client.write({"components": {"mill1": {"mill": {"A1": {"position": 10}}}, "mill2": {"mill": {"A1": {"position": -10}}}}})
        api.step(0.1)
        assert (a.a1, b.a1) == (10, -10)
        state = client.read()
        assert state.components.mill2.commands.mill.A1.position == -10
        assert requests.get(f"http://127.0.0.1:{api.port}/api/openapi.json").status_code == 200
    finally:
        api.stop()


def test_port_in_use_raises(running_server):
    api, _ = running_server
    other = SegmentApi(FakeCell(), port=api.port, host="127.0.0.1")
    with pytest.raises(RuntimeError, match="could not start"):
        other.start(timeout=5)


def test_stop_closes_streams(cell):
    api = SegmentApi(cell, port=free_port(), host="127.0.0.1")
    api.start()
    client = SegmentClient(f"http://127.0.0.1:{api.port}", api.component_commands_model, api.component_states_model)
    api.step(0.0)
    stream = client.stream(events=True)
    iterator = iter(stream)
    next(iterator)
    started = time.monotonic()
    api.stop()
    assert time.monotonic() - started < 4
    rest = list(iterator)
    assert [(e.state, e.reason) for e in rest] == [("stopped", "stopped")]  # the last event before the stream closes


def test_frames_without_components_are_delays(running_server, client, line):
    api, _ = running_server
    client.write([{"frame_id": 1, "components": {"line": {"belt": {"velocity": 1.0}}}},
                  {"frame_id": 2},           # no components: changes nothing, takes one step
                  {},                        # not even a frame_id
                  {"frame_id": 4, "components": {"line": {"belt": {"velocity": 2.0}}}}])
    belts = []
    for _ in range(4):
        response = api.step(0.01)
        belts.append((response.frame_id, line.belt))
    assert belts == [(1, 1.0), (2, 1.0), (None, 1.0), (4, 2.0)]


def test_transducer_errors_are_reported(running_server, client, line):
    api, _ = running_server
    client.write({"frame_id": 1, "components": {"line": {"belt": {"velocity": 1.0}}}})
    line.fail_on_write = "transducer"
    state = api.step(0.01).components.line
    assert state.status == "failed"
    assert [(e.code, e.component, e.transducer, e.message) for e in state.errors] == [
        ("WRITE_FAILED", "line", "belt", "RuntimeError: belt jammed")]
