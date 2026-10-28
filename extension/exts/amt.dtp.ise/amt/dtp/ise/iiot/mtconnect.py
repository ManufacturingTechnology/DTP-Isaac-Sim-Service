import http.client
import math
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from queue import Queue
import typing
from typing import Any, Dict, List, Optional, Tuple, Type
from pydantic import BaseModel, Field

from ..lib.control.transducer.models import (
    PrismaticJointPositionModel,
    PrismaticJointVelocityModel,
    RevoluteJointPositionModel,
    RevoluteJointVelocityModel,
)
from ..lib.logging import _logger

_JOINT_POSITION_MODELS = (RevoluteJointPositionModel, PrismaticJointPositionModel)
_JOINT_COMMAND_MODELS = _JOINT_POSITION_MODELS + (RevoluteJointVelocityModel, PrismaticJointVelocityModel)


class MtConnectClientConfiguration(BaseModel):
    url: str = Field(
        title="URL",
        description="The URL to the MTConnect agent."
    )
    interval: int = Field(
        title="Interval",
        description="The interval between the Agent sending data in milliseconds.",
    )
    device: str = Field(
        title="Device",
        description="The MTConnect device id or name in the agent.",
    )
    map: Dict[str, str] = Field(
        title="Map",
        description="A map of dataItemIds to actuator targets: '<actuator id>.<joint>' for a joint of an "
                    "articulation, '<actuator id>' for a single-joint actuator, or a bare joint name, which "
                    "is resolved if exactly one articulation of the component has that joint.",
    )
    commands_model: Type[BaseModel] = Field(
        title="Commands Model",
        description="The commands model of the automation component (AutomationComponent.command_model).",
    )


def _unwrap_optional(annotation: Any) -> Any:
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    return args[0] if typing.get_origin(annotation) is not None and len(args) == 1 else annotation


def _is_joint_command(model: Any) -> bool:
    """Whether ``model`` is a single joint command (a *JointPositionModel or *JointVelocityModel, or a
    constrained subclass of one)."""
    return isinstance(model, type) and issubclass(model, _JOINT_COMMAND_MODELS)


class MtConnectClient:
    """
    A simplified MTConnect client.

    Runs a single background thread that opens a streaming ``/sample`` connection
    to the agent, consumes the multipart XML documents as they arrive, translates
    each new observation into an instance of the component's commands model, and pushes it
    onto a :class:`queue.Queue` for a consumer to drain (e.g. into AutomationComponent.actuator_write).
    """

    # Read chunk size (bytes) for the streaming response.
    _CHUNK_SIZE = 1024
    # Socket timeout (seconds) for the non-streaming probe/current requests.
    _TIMEOUT_S = 30.0
    # 'heartbeat' (ms) requested on the /sample stream. A healthy agent emits an empty
    # document at least this often when no observation changes, so a live connection always
    # produces traffic at this cadence — which is what lets a silent stall be detected.
    _HEARTBEAT_MS = 1000
    # Socket read timeout (seconds) for the /sample stream. Set above the heartbeat (with
    # margin for jitter) so a connection that goes silent — still open but no longer
    # delivering data — trips the timeout and is surfaced as an exception instead of
    # blocking read1() forever. The non-streaming requests use _TIMEOUT_S instead.
    _STREAM_READ_TIMEOUT_S = 3.0 * (_HEARTBEAT_MS / 1000.0)
    # MTConnect unit tokens -> factor that converts a reported value to the canonical
    # model unit for its dimension: meters for a length, degrees for an angle. A mapped
    # dataItem feeds a joint target position, whose canonical unit is meters for a
    # linear/prismatic joint and degrees for an angular/revolute joint, so every mapped
    # dataItem must report either a length unit (canonicalized to meters) or an angle
    # unit (canonicalized to degrees). Which dimension is correct for a given joint is
    # the scene/config's responsibility; this client only canonicalizes whatever unit
    # the dataItem declares.
    _LENGTH_UNITS_TO_METERS = {
        "METER": 1.0,
        "CENTIMETER": 1e-2,
        "MILLIMETER": 1e-3,
        "MICROMETER": 1e-6,
        "INCH": 0.0254,
        "FOOT": 0.3048,
    }
    _ANGLE_UNITS_TO_DEGREES = {
        "DEGREE": 1.0,
        "RADIAN": 180.0 / math.pi,
        "MILLIRADIAN": (180.0 / math.pi) * 1e-3,
        "MICRORADIAN": (180.0 / math.pi) * 1e-6,
    }

    def __init__(self, config: MtConnectClientConfiguration):
        if not isinstance(config, MtConnectClientConfiguration):
            raise TypeError("config must be a MtConnectClientConfiguration")
        self._config = config

        self._thread: Optional[threading.Thread] = None
        self._thread_exception: Optional[Exception] = None
        self._stop_event = threading.Event()
        # Set by the thread once the probe + dataItem verification has finished
        # (whether it passed or raised) so start() can block until then.
        self._started_event = threading.Event()
        # Held so stop() can close the socket and unblock a pending read.
        self._response = None
        # dataItemId -> factor that converts a reported value to its canonical model
        # unit (meters for a length dataItem, degrees for an angle dataItem). Built and
        # validated from the probe in _thread_loop before streaming starts.
        self._to_canonical: Dict[str, float] = {}
        # Running full-state snapshot: dataItemId -> latest canonical value. Seeded from
        # /current and updated per streamed document, so every queued actuators model is a
        # COMPLETE pose (all mapped items), not a report-by-exception delta. A consumer can
        # then coalesce a burst to the newest queue item and apply it in a single write
        # without losing an item that changed only in an intermediate document.
        self._state: Dict[str, float] = {}
        self._queue: Queue[BaseModel] = Queue()
        # dataItemId -> (actuator id, joint field or None for a single-joint actuator, command model)
        self._targets: Dict[str, Tuple[str, Optional[str], Type[BaseModel]]] = self._resolve_targets()

    @property
    def exception(self) -> Optional[Exception]:
        return self._thread_exception

    @property
    def queue(self) -> Queue[BaseModel]:
        return self._queue

    def start(self):
        # Start thread, clear exit flag
        if self._thread is not None:
            raise RuntimeError("MtConnectClient is already started")
        self._thread_exception = None
        self._stop_event.clear()
        self._started_event.clear()
        self._thread = threading.Thread(
            target=self._thread_loop_wrapper,
            name="mtconnect-client",
            daemon=True,
        )
        self._thread.start()

        # Block until the thread has probed the agent and verified that every
        # mapped dataItem exists (and has valid units). If that step raised, the
        # exception is surfaced here and the (now-dead) thread is cleaned up.
        self._started_event.wait()
        if self._thread_exception is not None:
            self._thread.join()
            self._thread = None
            raise self._thread_exception

    def stop(self):
        # Stop thread, set exit flag
        self._stop_event.set()
        # Closing the response unblocks a read() that is waiting on the stream so
        # the thread can observe the exit flag and exit promptly.
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if self._thread_exception is not None:
            raise self._thread_exception

    def _thread_loop_wrapper(self) -> None:
        try:
            self._thread_loop()
        except Exception as ex:
            self._thread_exception = ex
            _logger.error(f"Error in MtConnectClient _thread_loop: {ex}", exception=ex)
        finally:
            # Unblock start() whether the probe verification passed (already set
            # below in _thread_loop) or the thread died before/while doing it.
            self._started_event.set()

    def _thread_loop(self) -> None:
        # Probe the agent and make sure it exposes every dataItemId in the map.
        # A failure to reach the agent surfaces as an exception here.
        probe_xml = self._http_get(self._probe_url())
        data_items = self._parse_probe_data_items(probe_xml)
        missing = sorted(d for d in self._config.map if d not in data_items)
        if missing:
            raise Exception(
                f"MTConnect agent at '{self._config.url}' is missing dataItemIds: {missing}"
            )

        # Get and check the units of every mapped dataItem, and precompute the factor
        # that canonicalizes each reported value (to meters for a length unit, degrees
        # for an angle unit). Values reported by the agent are in the dataItem's 'units',
        # so an unknown/non-length/non-angle unit can't be converted and fails loudly
        # here rather than mid-stream.
        self._to_canonical = {}
        for data_item_id in self._config.map:
            units = data_items[data_item_id]
            factor = self._unit_to_canonical_factor(units)
            if factor is None:
                raise Exception(
                    f"dataItemId '{data_item_id}' has units '{units}', which is not a "
                    f"known length or angle unit; expected one of "
                    f"{sorted(self._LENGTH_UNITS_TO_METERS) + sorted(self._ANGLE_UNITS_TO_DEGREES)}"
                )
            self._to_canonical[data_item_id] = factor

        # Establish the sequence to stream from so we only receive new data.
        current_xml = self._http_get(self._current_url())
        next_sequence = self._parse_next_sequence(current_xml)

        # Seed the running snapshot from /current (a complete, non-report-by-exception
        # sample) and queue it as the first full pose.
        self._state = self._parse_observation_values(current_xml)
        self._queue.put(self._construct_command_model(self._state))

        # The probe verification passed, and current passed; let start() return successfully. The rest
        # of this method (streaming) runs in the background.
        self._started_event.set()

        # Start a sample stream request.
        response = self._open_stream(self._sample_stream_url(next_sequence))
        self._response = response
        _logger.debug(f"MtConnectClient streaming from sequence {next_sequence}")
        try:
            # Iterate while streaming and no exit flag. A blocking read waits for
            # the next document when none is available yet, so there is no busy
            # poll; stop() closes the socket to break out.
            if self._stop_event.is_set():
                return
            for document in self._iter_stream_documents(response):
                if self._stop_event.is_set():
                    break

                # Collapse the document into {dataItemId: new value}, keeping only
                # the dataItemIds we were configured to translate.
                values = self._parse_observation_values(document)
                if values:
                    # Fold the document's changed items into the running snapshot and queue
                    # the COMPLETE pose (not just the delta), so a consumer that keeps only
                    # the newest queue item still applies every joint.
                    self._state.update(values)
                    self._queue.put(self._construct_command_model(self._state))
        finally:
            self._response = None
            try:
                response.close()
            except Exception:
                pass
            _logger.debug("MtConnectClient stream ended")

    def _resolve_targets(self) -> Dict[str, Tuple[str, Optional[str], Type[BaseModel]]]:
        """Resolve every map target to (actuator id, joint field or None, command model)."""
        actuators = {aid: _unwrap_optional(f.annotation) for aid, f in self._config.commands_model.model_fields.items()}
        joint_owners: Dict[str, List[str]] = {}
        for aid, model in actuators.items():
            if isinstance(model, type) and issubclass(model, BaseModel) and not _is_joint_command(model):
                for joint, info in model.model_fields.items():
                    if _is_joint_command(_unwrap_optional(info.annotation)):
                        joint_owners.setdefault(joint, []).append(aid)
        articulation_joints = sorted(f"{owner}.{joint}" for joint, owners in joint_owners.items() for owner in owners)

        targets: Dict[str, Tuple[str, Optional[str], Type[BaseModel]]] = {}
        for data_item_id, target in self._config.map.items():
            if "." in target:
                aid, joint = target.split(".", 1)
                container = actuators.get(aid)
                joint_info = None
                if isinstance(container, type) and issubclass(container, BaseModel):
                    joint_info = container.model_fields.get(joint)
                if joint_info is None or not _is_joint_command(_unwrap_optional(joint_info.annotation)):
                    raise ValueError(
                        f"MTConnect map target '{target}' (dataItemId '{data_item_id}') is not a joint of the "
                        f"component; articulation joints: {articulation_joints}"
                    )
                resolved = (aid, joint, _unwrap_optional(joint_info.annotation))
            elif target in actuators and _is_joint_command(actuators[target]):
                resolved = (target, None, actuators[target])
            elif len(joint_owners.get(target, [])) == 1:
                aid = joint_owners[target][0]
                resolved = (aid, target, _unwrap_optional(actuators[aid].model_fields[target].annotation))
            elif len(joint_owners.get(target, [])) > 1:
                raise ValueError(
                    f"MTConnect map target '{target}' (dataItemId '{data_item_id}') is a joint of several actuators "
                    f"{joint_owners[target]}; use '<actuator id>.{target}'"
                )
            else:
                raise ValueError(
                    f"MTConnect map target '{target}' (dataItemId '{data_item_id}') is not an actuator or articulation "
                    f"joint of the component (single-joint actuators: "
                    f"{sorted(a for a, m in actuators.items() if _is_joint_command(m))}, articulation joints: "
                    f"{articulation_joints})"
                )
            if not issubclass(resolved[2], _JOINT_POSITION_MODELS):
                raise ValueError(
                    f"MTConnect map target '{target}' (dataItemId '{data_item_id}') is velocity-driven; only "
                    f"position-driven joints are supported"
                )
            targets[data_item_id] = resolved
        return targets

    def _construct_command_model(self, values: Dict[str, float]) -> BaseModel:
        """Build a commands model from the latest dataItem values. Built without validation: a
        shadow mirrors the real machine, so values outside the simulated joint limits are kept."""
        fields: Dict[str, Any] = {}
        joints_by_actuator: Dict[str, Dict[str, BaseModel]] = {}
        for data_item_id, value in values.items():
            aid, joint, command_model = self._targets[data_item_id]
            command = command_model.model_construct(position=value)
            if joint is None:
                fields[aid] = command
            else:
                joints_by_actuator.setdefault(aid, {})[joint] = command
        for aid, joints in joints_by_actuator.items():
            container = _unwrap_optional(self._config.commands_model.model_fields[aid].annotation)
            fields[aid] = container.model_construct(**joints)
        return self._config.commands_model.model_construct(**fields)

    # --- URLs -------------------------------------------------------------

    def _base(self) -> str:
        # Sanitize localhost -> 127.0.0.1: on Windows 'localhost' resolves to IPv6 (::1)
        # first and stalls ~2s per connection on the fallback to IPv4, which makes the
        # /sample stream open with a stale 'from' sequence and replay a startup backlog.
        # Applied here (the single point every request URL derives from) so a config
        # override can't reintroduce the lag.
        return self._config.url.replace("localhost", "127.0.0.1").rstrip("/")

    def _device_base(self) -> str:
        # MTConnect scopes a request to a single device by prefixing the request type with
        # the device id or name in the PATH, e.g. /{device}/sample (not a query parameter,
        # which the agent ignores). An agent may expose multiple devices, so every request
        # this client makes is scoped to config.device to avoid picking up another device's
        # dataItems in the probe, the /current seed, or the stream. quote() encodes a device
        # name that contains spaces or other reserved characters.
        device = urllib.parse.quote(self._config.device, safe="")
        return f"{self._base()}/{device}"

    def _probe_url(self) -> str:
        return f"{self._device_base()}/probe"

    def _current_url(self) -> str:
        return f"{self._device_base()}/current"

    def _sample_stream_url(self, from_sequence: int) -> str:
        query = urllib.parse.urlencode({
            "from": from_sequence,
            "interval": self._config.interval,
            # Guarantee periodic traffic (empty documents) on an idle-but-live connection
            # so a stalled stream can be told apart from one that is merely quiet.
            "heartbeat": self._HEARTBEAT_MS,
        })
        return f"{self._device_base()}/sample?{query}"

    # --- HTTP -------------------------------------------------------------

    def _http_get(self, url: str) -> str:
        _logger.debug(f"MtConnectClient GET {url}")
        try:
            with urllib.request.urlopen(url, timeout=self._TIMEOUT_S) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as ex:
            raise Exception(f"MTConnect request HTTPError {ex.code} {ex.reason} for '{url}'") from ex
        except urllib.error.URLError as ex:
            raise Exception(f"MTConnect request URLError {ex.reason} for '{url}'") from ex

    def _open_stream(self, url: str):
        _logger.debug(f"MtConnectClient STREAM {url}")
        try:
            return urllib.request.urlopen(url, timeout=self._STREAM_READ_TIMEOUT_S)
        except urllib.error.HTTPError as ex:
            raise Exception(f"MTConnect stream HTTPError {ex.code} {ex.reason} for '{url}'") from ex
        except urllib.error.URLError as ex:
            raise Exception(f"MTConnect stream URLError {ex.reason} for '{url}'") from ex

    def _iter_stream_documents(self, response) -> Iterator[str]:
        """
        Yield one XML document per part of a ``multipart/x-mixed-replace`` stream.

        Buffers incoming bytes and emits the decoded body of each complete part,
        stripping the MIME part headers that precede each XML document.
        """
        content_type = response.headers.get("Content-Type", "")
        boundary = self._parse_boundary(content_type)
        if not boundary:
            raise Exception(f"MTConnect stream is not multipart (Content-Type: '{content_type}')")

        delimiter = f"--{boundary}".encode("ascii")
        end_delimiter = f"--{boundary}--".encode("ascii")
        buf = bytearray()

        while True:
            # read1() makes at most one underlying read and returns whatever bytes
            # are available right now. Plain read(n) instead blocks until it has n
            # bytes (or the stream closes), which batches many documents into one
            # burst and makes the delivered motion choppy regardless of chunk size.
            try:
                chunk = response.read1(self._CHUNK_SIZE)
            except (OSError, ValueError, http.client.HTTPException) as ex:
                # A read timeout (connection still open but silent past the heartbeat
                # window), a mid-stream socket error, or a read on the socket stop() just
                # closed. If we requested the stop this is expected — exit quietly; else
                # the feed is dead and must be surfaced rather than looking like a clean end.
                if self._stop_event.is_set():
                    return
                raise Exception(
                    f"MTConnect stream read failed (no data within "
                    f"{self._STREAM_READ_TIMEOUT_S:g}s, or connection dropped): {ex}"
                ) from ex
            if not chunk:
                # EOF: the server closed the connection. A /sample?interval= stream is
                # meant to stay open indefinitely, so this is expected only when we asked
                # to stop; otherwise the agent went away and the stream ended abnormally.
                if self._stop_event.is_set():
                    return
                raise Exception(f"MTConnect stream closed by server (EOF) for '{self._config.url}'")
            buf.extend(chunk)

            # Consume as many complete parts as the buffer holds.
            while True:
                start = buf.find(delimiter)
                if start == -1:
                    break

                # End-of-stream marker. The agent deliberately terminated the multipart
                # stream, which for an open-ended /sample?interval= request is itself
                # abnormal — surface it unless the stop was requested.
                if bytes(buf[start:start + len(end_delimiter)]) == end_delimiter:
                    if self._stop_event.is_set():
                        return
                    raise Exception(
                        f"MTConnect stream ended: server sent the end-of-stream marker "
                        f"for '{self._config.url}'"
                    )

                after_delimiter = start + len(delimiter)
                next_start = buf.find(delimiter, after_delimiter)
                if next_start == -1:
                    # Part not complete yet; drop bytes before the delimiter so the
                    # buffer doesn't grow unbounded, then read more.
                    del buf[:start]
                    break

                raw_part = bytes(buf[after_delimiter:next_start])
                del buf[:next_start]

                # A blank line separates the MIME part headers from the body.
                header_end = raw_part.find(b"\r\n\r\n")
                if header_end != -1:
                    body = raw_part[header_end + 4:]
                else:
                    header_end = raw_part.find(b"\n\n")
                    body = raw_part[header_end + 2:] if header_end != -1 else raw_part

                body = body.strip()
                if body:
                    yield body.decode("utf-8", errors="replace")

    @staticmethod
    def _parse_boundary(content_type: str) -> Optional[str]:
        for segment in content_type.split(";"):
            segment = segment.strip()
            if segment.lower().startswith("boundary="):
                return segment[len("boundary="):].strip('"')
        return None

    # --- XML parsing ------------------------------------------------------

    @staticmethod
    def _local_name(tag: str) -> str:
        # Strip the XML namespace, e.g. "{urn:mtconnect...}DataItem" -> "DataItem".
        return tag.split("}")[-1]

    @classmethod
    def _unit_to_canonical_factor(cls, units: Optional[str]) -> Optional[float]:
        # Factor that canonicalizes a value reported in `units` to the model unit for its
        # dimension: meters for a length unit, degrees for an angle unit. Returns None for
        # an unknown (or missing) unit so the caller can fail loudly.
        if units in cls._LENGTH_UNITS_TO_METERS:
            return cls._LENGTH_UNITS_TO_METERS[units]
        if units in cls._ANGLE_UNITS_TO_DEGREES:
            return cls._ANGLE_UNITS_TO_DEGREES[units]
        return None

    def _parse_probe_data_items(self, xml: str) -> Dict[str, Optional[str]]:
        # Map each DataItem to its declared 'units' (None when absent), keyed by BOTH
        # its id and its name so the config map can reference a dataItem by either.
        # Units are defined here in the probe, not on the individual sample observations.
        data_items: Dict[str, Optional[str]] = {}
        for el in ET.fromstring(xml).iter():
            if self._local_name(el.tag) == "DataItem" and "id" in el.attrib:
                units = el.attrib.get("units")
                data_items[el.attrib["id"]] = units
                name = el.attrib.get("name")
                if name is not None:
                    data_items[name] = units
        return data_items

    def _parse_next_sequence(self, xml: str) -> int:
        for el in ET.fromstring(xml).iter():
            if self._local_name(el.tag) == "Header":
                return int(el.attrib["nextSequence"])
        raise Exception("MTConnect response has no Header/nextSequence")

    def _parse_observation_values(self, xml: str) -> Dict[str, float]:
        """
        Collapse a stream document into {key: canonical_value} for the configured map
        keys only, canonicalizing each value from its dataItem's units (validated at
        probe time) to meters (length) or degrees (angle). Each observation carries both
        its dataItem's id (dataItemId) and its name; since the config map may reference a
        dataItem by either, both are matched and the value is stored under whichever key(s)
        the map uses. When a key appears more than once in the document the last (newest)
        value wins. Non-numeric observations (e.g. UNAVAILABLE or categorical events) are
        skipped.
        """
        values: Dict[str, float] = {}
        for el in ET.fromstring(xml).iter():
            text = (el.text or "").strip()
            for key in (el.attrib.get("dataItemId"), el.attrib.get("name")):
                if key is None or key not in self._config.map:
                    continue
                try:
                    values[key] = float(text) * self._to_canonical[key]
                except (TypeError, ValueError):
                    _logger.trace(
                        f"MtConnectClient skipping non-numeric value '{text}' for '{key}'"
                    )
        return values
