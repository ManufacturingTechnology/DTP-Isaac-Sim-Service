"""The segment protocol (docs/src/Agents/Specs/segment-protocol.md): sequence numbers and
instance ids, per-component status and typed errors, the halt-on-failure policy (ADR-0021), confirmed
configuration writes, stream events and resuming, and the retransmission buffer."""
import threading
import time

import pytest
import requests

from amt.dtp.ise.lib.control.core.client import CoreApiError
from amt.dtp.ise.lib.control.segment.client import SegmentClient
from amt.dtp.ise.lib.control.segment.engine import OutOfRangeError, SegmentEngine, SegmentHaltedError
from amt.dtp.ise.lib.control.segment.models import ConfigEvent, ResetEvent, StatusEvent
from amt.dtp.ise.lib.control.segment.server import SegmentApi

from .conftest import FakeCell, FakeLine, free_port


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


@pytest.fixture
def client(running_server):
    api, _ = running_server
    return SegmentClient(f"http://127.0.0.1:{api.port}", api.component_commands_model, api.component_states_model,
                         configs_model=api.component_configs_model)


def url(api, path):
    return f"http://127.0.0.1:{api.port}/api/segment{path}"


# ---- sequence numbers --------------------------------------------------------------------------------

def test_every_event_gets_the_next_sequence_and_the_instance_id(running_server):
    api, _ = running_server
    frames = [api.step(0.01) for _ in range(3)]
    assert [f.sequence for f in frames] == [1, 2, 3]
    assert {f.instance_id for f in frames} == {api.segment.instance_id}
    api.clear()  # a status event takes a sequence too
    assert api.step(0.01).sequence == 5


def test_a_new_engine_has_a_new_instance_id(cell, line):
    a, b = SegmentEngine({"mill": cell}), SegmentEngine({"mill": FakeCell()})
    assert a.instance_id != b.instance_id
    assert a.step(0.01).sequence == 1 and b.step(0.01).sequence == 1


# ---- statuses and typed errors ---------------------------------------------------------------------

class IKUnreachable(ValueError):
    error_code = "IK_UNREACHABLE"


def test_error_codes_come_from_the_exception(cell, line):
    engine = SegmentEngine({"mill": cell, "line": line})

    def fail(value):
        raise IKUnreachable("no IK solution for (9, 9, 9)")

    line.actuator_write = fail
    engine.enqueue([{"frame_id": 3, "components": {"line": {"belt": {"velocity": 1}}}}])
    frame = engine.step(0.01)
    [error] = frame.components.line.errors
    assert (error.code, error.component, error.transducer, error.sequence) == ("IK_UNREACHABLE", "line", None, frame.sequence)
    assert error.message == "IKUnreachable: no IK solution for (9, 9, 9)"


# ---- the halt-on-failure policy (ADR-0021) -----------------------------------------------------------

def test_a_failed_frame_halts_until_resumed(running_server, client, line):
    api, _ = running_server
    client.write([{"frame_id": i, "components": {"line": {"belt": {"velocity": float(i)}}}} for i in (1, 2, 3)])
    line.fail_on_write = "transducer"
    failed = api.step(0.01)
    assert failed.frame_id == 1 and failed.halted and failed.components.line.status == "failed"
    line.fail_on_write = False

    # Halted: steps still run and publish (nothing applied), the remaining frames stay queued.
    for _ in range(3):
        idle = api.step(0.01)
        assert idle.frame_id is None and idle.halted and idle.components.line.status == "not_addressed"
    assert [f.frame_id for f in api.pending()] == [2, 3] and line.belt == 0.0

    info = client.segment_info()
    assert info.halted and info.halt.sequence == failed.sequence and info.halt.frame_id == 1
    assert [(e.code, e.transducer) for e in info.halt.errors] == [("WRITE_FAILED", "belt")]
    assert client.read().halted

    # Writes are still accepted (and say the segment is halted); execute fails fast.
    receipt = client.write({"frame_id": 4, "components": {}})
    assert receipt.halted and receipt.queue_length == 3
    with pytest.raises(CoreApiError) as error:
        client.execute({"frame_id": 5}, timeout=5)
    assert error.value.status_code == 409 and error.value.code == "HALTED" and "HALTED" in str(error.value)

    status = client.resume()
    assert (status.state, status.queue_length, status.halt) == ("running", 3, None)
    assert client.resume().state == "running"  # idempotent
    assert [api.step(0.01).frame_id for _ in range(3)] == [2, 3, 4] and line.belt == 3.0
    assert not client.segment_info().halted


def test_clear_releases_a_halt(running_server, client, line):
    api, _ = running_server
    client.write([{"frame_id": 1, "components": {"line": {"belt": {"velocity": 1}}}}, {"frame_id": 2}])
    line.fail_on_write = True
    assert api.step(0.01).halted
    line.fail_on_write = False
    assert [f.frame_id for f in client.clear()] == [2]
    assert not api.segment.halted and not api.step(0.01).halted
    client.write({"frame_id": 3, "components": {"line": {"belt": {"velocity": 2}}}})
    assert api.step(0.01).frame_id == 3 and line.belt == 2.0


def test_read_failures_do_not_halt(running_server, client, line):
    api, _ = running_server
    line.fail_on_read = True
    client.write([{"frame_id": 1, "components": {"line": {"belt": {"velocity": 1}}}}, {"frame_id": 2}])
    first, second = api.step(0.01), api.step(0.01)
    assert (first.frame_id, second.frame_id) == (1, 2) and not second.halted
    assert first.components.line.status == "applied" and first.components.line.errors[0].code == "READ_FAILED"


def test_execute_while_halted_raises_in_the_engine(cell, line):
    engine = SegmentEngine({"mill": cell, "line": line})
    line.fail_on_write = True
    engine.enqueue([{"components": {"line": {"belt": {"velocity": 1}}}}])
    engine.step(0.01)
    with pytest.raises(SegmentHaltedError, match="Resume"):
        engine.submit({"frame_id": 1})


def test_halt_and_resume_are_status_events(running_server, client, line):
    api, _ = running_server
    api.step(0.01)
    with client.stream(events=True) as stream:
        iterator = iter(stream)
        assert next(iterator).sequence == 1  # the latest frame on connect
        client.write([{"frame_id": 1, "components": {"line": {"belt": {"velocity": 1}}}}, {"frame_id": 2}])
        line.fail_on_write = True
        api.step(0.01)
        line.fail_on_write = False
        client.resume()
        api.step(0.01)
        events = [next(iterator) for _ in range(4)]
    failed, halted, resumed, applied = events
    assert failed.frame_id == 1 and failed.halted
    assert isinstance(halted, StatusEvent) and (halted.state, halted.reason) == ("halted", "frame_failed")
    assert halted.halt.sequence == failed.sequence and halted.halt.frame_id == 1 and halted.queue_length == 1
    assert isinstance(resumed, StatusEvent) and (resumed.state, resumed.reason) == ("running", "resumed")
    assert applied.frame_id == 2 and not applied.halted
    assert [e.sequence for e in events] == [2, 3, 4, 5]


# ---- configuration -------------------------------------------------------------------------------------

def test_config_read_returns_the_configurable_components(running_server, client):
    configuration = client.config_read()
    assert configuration.config_revision == 0 and configuration.instance_id == running_server[0].segment.instance_id
    assert configuration.components.model_dump() == {"line": {"belt": {"max_velocity": 1.0, "reversed": False}}}


def test_config_write_is_confirmed_at_the_next_step(running_server, client, line):
    api, stepper = running_server
    client.write([{"frame_id": 1}, {"frame_id": 2}])  # applied even while frames are queued
    stepper.start()
    event = client.config_write({"line": {"belt": {"max_velocity": 0.25}}})
    assert isinstance(event, ConfigEvent) and event.config_revision == 1
    assert event.components["line"].status == "applied" and event.components["line"].applied == ["belt"]
    assert line.belt_config == {"max_velocity": 0.25, "reversed": False}  # partial: 'reversed' unchanged
    assert client.config_read().components.line.belt.max_velocity == 0.25
    stepper.pause()
    time.sleep(0.05)
    # The response frames carry the revision from the effective sequence on.
    frame = client.read()
    assert frame.config_revision == 1 and frame.sequence >= event.effective_sequence == event.sequence + 1


def test_failed_config_write_reports_the_transducer(running_server, client, line):
    api, stepper = running_server
    stepper.start()
    event = client.config_write({"line": {"belt": {"reversed": True}}})
    outcome = event.components["line"]
    assert outcome.status == "failed" and outcome.applied == []
    [error] = outcome.errors
    assert (error.code, error.component, error.transducer, error.sequence) == (
        "CONFIG_WRITE_FAILED", "line", "belt", event.sequence)
    assert event.config_revision == 0  # nothing applied: unchanged


def test_config_write_is_validated_and_times_out_without_steps(running_server, client):
    api, _ = running_server
    response = requests.post(url(api, "/config/write"), json={"mill": {}})  # 'mill' has no configuration
    assert response.status_code == 422
    with pytest.raises(CoreApiError) as error:
        client.config_write({"line": {"belt": {"max_velocity": 0.5}}}, timeout=0.2)
    assert error.value.status_code == 504 and "withdrawn" in str(error.value)
    api.step(0.01)
    assert client.config_read().config_revision == 0  # the withdrawn write was not applied


def test_config_events_come_before_the_frame_they_apply_to(cell, line):
    engine = SegmentEngine({"mill": cell, "line": line})
    seen = []
    engine.add_listener(lambda event: seen.append((event.event, event.sequence)))
    first = engine.config_write({"line": {"belt": {"max_velocity": 0.5}}})
    second = engine.config_write({"line": {"belt": {"max_velocity": 0.7}}})
    frame = engine.step(0.01)
    assert seen == [("config", 1), ("config", 2), ("frame", 3)]
    assert (first.result().config_revision, second.result().config_revision, frame.config_revision) == (1, 2, 2)
    assert first.result().effective_sequence == second.result().effective_sequence == frame.sequence


# ---- stream resume ---------------------------------------------------------------------------------------

def test_resume_after_sends_buffered_events_then_live(running_server, client):
    api, _ = running_server
    for _ in range(5):
        api.step(0.01)
    with client.stream(resume_after=2, instance_id=api.segment.instance_id) as stream:
        iterator = iter(stream)
        replayed = [next(iterator).sequence for _ in range(3)]
        api.step(0.01)
        live = next(iterator).sequence
    assert replayed == [3, 4, 5] and live == 6  # no duplicates, no gaps
    assert stream.last_sequence == 6 and stream.instance_id == api.segment.instance_id


def test_last_event_id_resumes_like_eventsource(running_server):
    api, _ = running_server
    for _ in range(4):
        api.step(0.01)
    headers = {"Last-Event-ID": f"{api.segment.instance_id}:2", "Accept": "text/event-stream"}
    with requests.get(url(api, "/stream"), headers=headers, stream=True, timeout=(5, 5)) as response:
        lines = []
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("id:"):
                lines.append(line)
            if len(lines) == 2:
                break
    assert lines == [f"id: {api.segment.instance_id}:3", f"id: {api.segment.instance_id}:4"]


def test_resuming_another_instance_sends_a_reset(running_server, client):
    api, _ = running_server
    api.step(0.01)
    with client.stream(resume_after=10, instance_id="stale", events=True) as stream:
        iterator = iter(stream)
        reset = next(iterator)
        frame = next(iterator)  # then live, from the latest frame
    assert isinstance(reset, ResetEvent) and reset.code == "INSTANCE_CHANGED"
    assert reset.header.instance_id == api.segment.instance_id and frame.sequence == 1


def test_resuming_from_an_evicted_sequence_is_out_of_range(running_server, client):
    api, _ = running_server
    for _ in range(25):  # the buffer holds 20
        api.step(0.01)
    with pytest.raises(CoreApiError) as error:
        client.stream(resume_after=2)
    assert error.value.status_code == 404 and error.value.code == "OUT_OF_RANGE"
    assert error.value.detail["header"]["first_sequence"] == 6 and error.value.detail["header"]["last_sequence"] == 25
    with pytest.raises(CoreApiError) as error:
        client.stream(resume_after=99)  # later than the last event
    assert error.value.code == "OUT_OF_RANGE"
    # clamp: a reset event naming the lost range, then the oldest buffered event.
    with client.stream(resume_after=2, clamp=True, events=True) as stream:
        iterator = iter(stream)
        reset, oldest = next(iterator), next(iterator)
    assert (reset.code, reset.lost_from, reset.lost_to) == ("OUT_OF_RANGE", 3, 5) and oldest.sequence == 6


def test_resume_applies_the_filters(running_server, client):
    api, _ = running_server
    api.step(0.01)
    client.write({"frame_id": 1})
    api.step(0.01)
    api.step(0.01)
    with client.stream(resume_after=0, executed_only=True) as stream:
        assert next(iter(stream)).frame_id == 1


def test_heartbeats_carry_next_sequence(running_server, client, monkeypatch):
    from amt.dtp.ise.lib.control.segment import server
    monkeypatch.setattr(server, "HEARTBEAT_INTERVAL", 0.1)
    api, _ = running_server
    api.step(0.01)
    with client.stream() as stream:
        iterator = iter(stream)
        next(iterator)
        thread = threading.Thread(target=lambda: (time.sleep(0.5), api.step(0.01)))
        thread.start()
        assert next(iterator).sequence == 2
        thread.join()
    assert stream.next_sequence == 3


# ---- the retransmission buffer -------------------------------------------------------------------------

def test_retransmit_by_list_returns_found_and_missing(running_server, client):
    api, _ = running_server
    for _ in range(25):
        api.step(0.01)
    result = client.retransmit(sequences=[8, 4, 15, 8, 30])
    assert [e.sequence for e in result.events] == [8, 15]
    assert result.missing == [4, 30]
    assert (result.header.first_sequence, result.header.last_sequence, result.header.next_sequence) == (6, 25, 26)
    assert result.events[0].components.line.actuals.probe.distance == 0.5  # parsed into the frame model


def test_retransmit_by_range(running_server, client):
    api, _ = running_server
    for _ in range(10):
        api.step(0.01)
    api.clear()  # a status event (sequence 11)
    result = client.retransmit(from_=9)
    assert [e.sequence for e in result.events] == [9, 10, 11] and not result.truncated
    assert isinstance(result.events[-1], StatusEvent) and result.events[-1].reason == "cleared"
    result = client.retransmit(from_=2, to=7, count=3)
    assert [e.sequence for e in result.events] == [2, 3, 4] and result.truncated
    assert client.retransmit(from_=12).events == []  # nothing newer yet


def test_retransmit_out_of_range_and_instance(running_server, client):
    api, _ = running_server
    for _ in range(25):
        api.step(0.01)
    with pytest.raises(CoreApiError) as error:
        client.retransmit(from_=2)
    assert error.value.status_code == 404 and error.value.code == "OUT_OF_RANGE"
    assert error.value.detail["header"]["buffer_size"] == 20
    assert client.retransmit(from_=2, to=7, clamp=True).events[0].sequence == 6
    with pytest.raises(CoreApiError) as error:
        client.retransmit(from_=99)
    assert error.value.code == "OUT_OF_RANGE"
    with pytest.raises(CoreApiError) as error:
        client.retransmit(sequences=[6], instance_id="stale")
    assert error.value.status_code == 409 and error.value.code == "INSTANCE_CHANGED"
    assert requests.get(url(api, "/retransmit")).status_code == 400
    assert requests.get(url(api, "/retransmit"), params={"sequences": "1", "from": 1}).status_code == 400


def test_buffer_size_zero_disables_the_buffer(cell, line):
    engine = SegmentEngine({"mill": cell, "line": line}, buffer_size=0)
    for _ in range(3):
        engine.step(0.01)
    header = engine.header()
    assert (header.buffer_size, header.first_sequence, header.last_sequence, header.next_sequence) == (0, None, None, 4)
    assert engine.events_since(3) == ([], None)  # resuming without a gap still works
    with pytest.raises(OutOfRangeError):
        engine.events_since(2)
    events, lost = engine.events_since(1, clamp=True)
    assert events == [] and lost == (2, 3)
    _, found, missing, _ = engine.retransmit(sequences=[1, 2])
    assert found == [] and missing == [1, 2]


def test_buffer_size_zero_over_http(cell):
    api = SegmentApi(cell, port=free_port(), host="127.0.0.1", buffer_size=0)
    api.start()
    try:
        client = SegmentClient(f"http://127.0.0.1:{api.port}", api.component_commands_model, api.component_states_model)
        api.step(0.01)
        assert client.segment_info().buffer.buffer_size == 0
        with client.stream(resume_after=1) as stream:
            iterator = iter(stream)
            api.step(0.01)
            assert next(iterator).sequence == 2
    finally:
        api.stop()


def test_engine_validates_its_arguments(cell):
    with pytest.raises(ValueError, match="buffer_size"):
        SegmentEngine(cell, buffer_size=-1)
    with pytest.raises(ValueError, match="subscriber_queue_size"):
        SegmentApi(cell, subscriber_queue_size=0)
    engine = SegmentEngine({"mill": cell}, name="Cell")
    api = SegmentApi(engine)  # an engine built elsewhere
    assert api.segment is engine and api.title == "Cell" and api.model_prefix == "Cell"
    with pytest.raises(ValueError, match="differs from the engine"):
        SegmentApi(engine, model_prefix="Other")
