import asyncio
import os
import pathlib
import re
import shutil
import sys
import threading
from typing import List, Optional, Tuple

from isaacsim.gui.components.element_wrappers import (
    Button,
    IntField,
    TextBlock,
)
from isaacsim.gui.components.ui_utils import get_style
import omni
import omni.kit.app
import omni.kit.renderer_capture
import omni.kit.viewport.utility
import omni.ui
import omni.usd

from .core import ExtensionWindow
from ..lib.logging import _logger
from ..service.subscription import SubscriptionService, SubscriptionSet


# File/folder naming: ISS-Capture-###(.mp4). The intermediate PNG frames live in a
# folder "ISS-Capture-###/" and the encoded output is "ISS-Capture-###.mp4".
_CAPTURE_BASENAME = "ISS-Capture-"
_INDEX_RE = re.compile(r"^ISS-Capture-(\d+)$", re.IGNORECASE)
_MIN_INDEX_PAD = 3
_FRAME_NAME_FMT = "frame.{:05d}.png"
_DEFAULT_FPS = 30


def _get_videos_dir() -> pathlib.Path:
    """Resolve the OS-standard Videos folder, creating it if missing.

    Windows: %USERPROFILE%\\Videos. Linux: $XDG_VIDEOS_DIR if set, else ~/Videos.
    """
    home = pathlib.Path.home()
    if sys.platform == "win32":
        videos_dir = home / "Videos"
    else:
        xdg = os.environ.get("XDG_VIDEOS_DIR")
        if xdg:
            videos_dir = pathlib.Path(os.path.expandvars(os.path.expanduser(xdg)))
        else:
            videos_dir = home / "Videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    return videos_dir


def _next_capture_index(videos_dir: pathlib.Path) -> int:
    """Scan the Videos folder for existing ISS-Capture-### files/folders and return
    the next monotonically increasing index."""
    max_idx = -1
    try:
        for entry in videos_dir.iterdir():
            name = entry.name
            if entry.is_file() and name.lower().endswith(".mp4"):
                name = name[:-4]
            match = _INDEX_RE.match(name)
            if match:
                max_idx = max(max_idx, int(match.group(1)))
    except OSError as ex:
        _logger.error("Failed to scan Videos folder for capture index", exception=ex)
    return max_idx + 1


def _format_base_name(index: int) -> str:
    """Zero-pad the index to at least _MIN_INDEX_PAD digits (widening if needed)."""
    width = max(_MIN_INDEX_PAD, len(str(index)))
    return f"{_CAPTURE_BASENAME}{index:0{width}d}"


class VideoCapturer:
    """Records the active (perspective) viewport to an mp4.

    Phase 1 (per tick): schedule an async viewport->PNG capture. The renderer's
    capture pipeline performs the GPU readback and PNG write off the main thread.
    Phase 2 (on stop): drain in-flight captures, then encode the PNG sequence to
    mp4 on a dedicated worker thread using the Isaac-bundled video_encoding
    interface, and delete the intermediate PNG folder.

    Capture is run asynchronously (set_capture_sync(False)) so the render loop does
    not block on each frame's GPU readback -- this is how Omniverse's own movie
    capture drives PNG capture and is what keeps the timeline smooth.
    """

    STATE_IDLE = "idle"
    STATE_RECORDING = "recording"
    STATE_FINALIZING = "finalizing"
    STATE_ENCODING = "encoding"
    STATE_DONE = "done"
    STATE_CANCELLED = "cancelled"
    STATE_ERROR = "error"

    # States in which no further work is pending (safe to start a new capture).
    _TERMINAL_STATES = (STATE_DONE, STATE_CANCELLED, STATE_ERROR)

    def __init__(self, fps: int):
        self._fps = max(1, int(fps))

        self._videos_dir = _get_videos_dir()
        self._index = _next_capture_index(self._videos_dir)
        self._base_name = _format_base_name(self._index)
        self._frames_dir = self._videos_dir / self._base_name
        self._output_path = self._videos_dir / f"{self._base_name}.mp4"

        self._sub: Optional[SubscriptionSet] = None
        self._frame_number = 0
        self._last_capture: Optional[object] = None
        self._encode_thread: Optional[threading.Thread] = None
        self._resolution: Optional[Tuple[int, int]] = None

        self._abort = False
        self._state = self.STATE_IDLE
        self._error: Optional[str] = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def output_path(self) -> pathlib.Path:
        return self._output_path

    @property
    def frame_count(self) -> int:
        return self._frame_number

    @property
    def status_text(self) -> str:
        res = f" @ {self._resolution[0]}x{self._resolution[1]}" if self._resolution else ""
        if self._state == self.STATE_RECORDING:
            return f"Recording - {self._frame_number} frame(s){res}\n{self._output_path}"
        if self._state == self.STATE_FINALIZING:
            return f"Finalizing capture ({self._frame_number} frame(s))..."
        if self._state == self.STATE_ENCODING:
            return f"Encoding {self._frame_number} frame(s){res}...\n{self._output_path}"
        if self._state == self.STATE_DONE:
            return f"Saved: {self._output_path}"
        if self._state == self.STATE_CANCELLED:
            return "Cancelled."
        if self._state == self.STATE_ERROR:
            return f"Error: {self._error}"
        return "Idle"

    def start(self) -> None:
        self._frames_dir.mkdir(parents=True, exist_ok=True)

        # Capture asynchronously so the render loop does not stall on each readback.
        try:
            renderer_capture = omni.kit.renderer_capture.acquire_renderer_capture_interface()
            renderer_capture.set_capture_sync(False)
        except Exception as ex:
            _logger.error("Failed to set asynchronous capture mode", exception=ex)

        # Record the capture resolution up front for the status display.
        self._resolution = self._read_resolution()

        self._sub = SubscriptionService().create_subscription_set("Video Capture")
        self._sub.on_timeline_current_time_ticked_permanent = self.on_timeline_ticked_permanent
        self._sub.enable()

        self._state = self.STATE_RECORDING
        _logger.debug(f"Video capture started -> {self._output_path}")

    def _read_resolution(self) -> Optional[Tuple[int, int]]:
        try:
            viewport = omni.kit.viewport.utility.get_active_viewport()
            if viewport is not None and viewport.resolution:
                width, height = viewport.resolution
                if width and height:
                    return (int(width), int(height))
        except Exception:
            pass
        return None

    def on_timeline_ticked_permanent(self, current_time: float, delta_time: float) -> None:
        # Capture the perspective viewport for this tick. Scheduling is cheap; the
        # renderer does the readback + PNG write asynchronously.
        try:
            viewport = omni.kit.viewport.utility.get_active_viewport()
            if viewport is None:
                return
            if self._resolution is None:
                self._resolution = self._read_resolution()
            frame_path = self._frames_dir / _FRAME_NAME_FMT.format(self._frame_number)
            capture = omni.kit.viewport.utility.capture_viewport_to_file(viewport, str(frame_path))
            if capture is not None:
                # Keep only the most recent capture; the renderer completes scheduled
                # captures in order, so awaiting the last one drains the whole queue.
                self._last_capture = capture
            self._frame_number += 1
        except Exception as ex:
            _logger.error("Failed to capture viewport frame", exception=ex)

    def stop(self) -> None:
        if self._sub is not None:
            self._sub.destroy()
            self._sub = None

        if self._state != self.STATE_RECORDING:
            return

        self._state = self.STATE_FINALIZING
        # Drain in-flight captures then encode, deferred off the current call stack.
        asyncio.ensure_future(self._finalize_async())

    def cancel(self) -> None:
        """Abort the capture, discarding frames and any partial mp4."""
        self._abort = True
        if self._sub is not None:
            self._sub.destroy()
            self._sub = None

        # If we haven't started encoding yet, clean up immediately. Otherwise the
        # encode worker observes _abort, stops, and cleans up.
        if self._state in (self.STATE_RECORDING, self.STATE_IDLE):
            self._state = self.STATE_CANCELLED
            self._cleanup_partial()
        _logger.debug("Video capture cancelled")

    def _cleanup_partial(self) -> None:
        shutil.rmtree(self._frames_dir, ignore_errors=True)
        try:
            if self._output_path.exists():
                self._output_path.unlink()
        except OSError:
            pass

    async def _finalize_async(self) -> None:
        # Wait for the last scheduled capture (and thus all prior ones) to flush to
        # disk, then let a couple of update cycles settle any tail writes.
        capture = self._last_capture
        self._last_capture = None
        if capture is not None:
            wait_fn = getattr(capture, "wait_for_result", None)
            if wait_fn is not None:
                try:
                    await wait_fn()
                except Exception:
                    pass
        for _ in range(2):
            await omni.kit.app.get_app().next_update_async()

        if self._abort:
            self._state = self.STATE_CANCELLED
            self._cleanup_partial()
            return

        self._encode_thread = threading.Thread(
            target=self._encode, name="AMT DTP Video Encode", daemon=True
        )
        self._encode_thread.start()

    def _encode(self) -> None:
        try:
            from video_encoding import get_video_encoding_interface
        except ImportError as ex:
            self._error = (
                "Video encoding unavailable. The 'omni.videoencoding' extension is not "
                "enabled; the captured PNG frames were kept."
            )
            self._state = self.STATE_ERROR
            _logger.error(self._error, exception=ex)
            return

        api = get_video_encoding_interface()
        if api is None:
            self._error = "Video encoding interface not available; PNG frames were kept."
            self._state = self.STATE_ERROR
            _logger.error(self._error)
            return

        frames: List[str] = []
        for i in range(self._frame_number):
            frame_path = self._frames_dir / _FRAME_NAME_FMT.format(i)
            if frame_path.is_file():
                frames.append(str(frame_path))

        if not frames:
            self._error = "No frames were captured."
            self._state = self.STATE_ERROR
            _logger.error(self._error)
            return

        self._state = self.STATE_ENCODING
        _logger.debug(f"Encoding {len(frames)} frames -> {self._output_path} @ {self._fps} fps")

        encoded = False
        try:
            if not api.start_encoding(str(self._output_path), self._fps, len(frames), True):
                raise RuntimeError("start_encoding() returned failure")
            for frame_path in frames:
                if self._abort:
                    break
                api.encode_next_frame_from_file(frame_path)
            encoded = not self._abort
        except Exception as ex:
            self._error = f"Encoding failed: {ex}"
            _logger.error("Video encoding failed", exception=ex)
        finally:
            try:
                api.finalize_encoding()
            except Exception as ex:
                _logger.error("finalize_encoding() failed", exception=ex)

        if self._abort:
            self._cleanup_partial()
            self._state = self.STATE_CANCELLED
        elif encoded:
            shutil.rmtree(self._frames_dir, ignore_errors=True)
            self._state = self.STATE_DONE
            _logger.debug(f"Video capture saved -> {self._output_path}")
        else:
            self._state = self.STATE_ERROR


class VideoCaptureWindow(ExtensionWindow):

    def __init__(self):
        super().__init__("Video Capture")
        self._video_capturer: Optional[VideoCapturer] = None
        self._fps_field: Optional[IntField] = None
        self._start_button: Optional[Button] = None
        self._stop_button: Optional[Button] = None
        self._cancel_button: Optional[Button] = None
        self._status_block: Optional[TextBlock] = None
        self._status_task: Optional[asyncio.Task] = None

    def _default_fps(self) -> int:
        """Default FPS from the open stage's Time Codes Per Second, else _DEFAULT_FPS."""
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                tcps = int(round(stage.GetTimeCodesPerSecond()))
                if tcps > 0:
                    return tcps
        except Exception as ex:
            _logger.error("Failed to read stage Time Codes Per Second", exception=ex)
        return _DEFAULT_FPS

    def construct(self) -> None:
        with omni.ui.VStack(style=get_style(), spacing=5, height=0):

            self._fps_field = IntField(
                "Frame Rate (FPS)",
                tooltip="Playback frame rate for the encoded mp4. Defaults to the stage's Time Codes Per Second.",
                default_value=self._default_fps(),
                lower_limit=1,
            )

            self._start_button = Button(
                "Start",
                "Record",
                tooltip="Begin capturing the perspective viewport to the Videos folder.",
                on_click_fn=self._start_capture,
            )

            self._stop_button = Button(
                "Stop",
                "Stop",
                tooltip="Stop capturing and encode the mp4.",
                on_click_fn=self._stop_capture,
            )

            self._cancel_button = Button(
                "Cancel",
                "Cancel",
                tooltip="Abort the capture and discard the frames (no mp4 is written).",
                on_click_fn=self._cancel_capture,
            )

            self._status_block = TextBlock(
                "Status",
                text="Idle",
                num_lines=3,
                include_copy_button=False,
            )

        self._update_buttons()
        self._refresh_status()

    def _update_buttons(self) -> None:
        state = self._video_capturer.state if self._video_capturer is not None else VideoCapturer.STATE_IDLE
        recording = state == VideoCapturer.STATE_RECORDING
        working = state in (
            VideoCapturer.STATE_RECORDING,
            VideoCapturer.STATE_FINALIZING,
            VideoCapturer.STATE_ENCODING,
        )
        if self._start_button is not None:
            self._start_button.button.enabled = not working
        if self._stop_button is not None:
            self._stop_button.button.enabled = recording
        if self._cancel_button is not None:
            self._cancel_button.button.enabled = working
        if self._fps_field is not None:
            self._fps_field.int_field.enabled = not working

    def _refresh_status(self) -> None:
        if self._status_block is None:
            return
        if self._video_capturer is None:
            self._status_block.set_text("Idle")
        else:
            self._status_block.set_text(self._video_capturer.status_text)

    def _start_capture(self) -> None:
        if self._video_capturer is not None and self._video_capturer.state not in VideoCapturer._TERMINAL_STATES:
            return

        if omni.kit.viewport.utility.get_active_viewport() is None:
            if self._status_block is not None:
                self._status_block.set_text("Error: No active viewport to capture.")
            return

        fps = self._fps_field.get_value() if self._fps_field is not None else _DEFAULT_FPS
        try:
            self._video_capturer = VideoCapturer(fps)
            self._video_capturer.start()
        except Exception as ex:
            _logger.error("Failed to start video capture", exception=ex)
            if self._status_block is not None:
                self._status_block.set_text(f"Error: {ex}")
            self._video_capturer = None
            return

        self._update_buttons()
        self._refresh_status()
        self._start_status_loop()

    def _stop_capture(self) -> None:
        if self._video_capturer is None:
            return
        self._video_capturer.stop()
        self._update_buttons()
        self._refresh_status()

    def _cancel_capture(self) -> None:
        if self._video_capturer is None:
            return
        self._video_capturer.cancel()
        self._update_buttons()
        self._refresh_status()

    def _start_status_loop(self) -> None:
        if self._status_task is not None and not self._status_task.done():
            return
        self._status_task = asyncio.ensure_future(self._status_loop())

    async def _status_loop(self) -> None:
        app = omni.kit.app.get_app()
        while True:
            capturer = self._video_capturer
            if capturer is None:
                break
            self._refresh_status()
            if capturer.state in VideoCapturer._TERMINAL_STATES:
                self._update_buttons()
                break
            await app.next_update_async()

    def deconstruct(self) -> None:
        # Window is being hidden/torn down: stop recording and let encoding finish
        # in the background. The encode thread is a daemon that only touches disk +
        # the capturer object (never the UI), so it's safe to leave running; frames
        # are only deleted on successful encode, so an interrupted run is recoverable.
        if self._status_task is not None:
            self._status_task.cancel()
            self._status_task = None
        if self._video_capturer is not None:
            try:
                if self._video_capturer.state == VideoCapturer.STATE_RECORDING:
                    self._video_capturer.stop()
            except Exception as ex:
                _logger.error("Error finalizing video capture on window close", exception=ex)
        self._fps_field = None
        self._start_button = None
        self._stop_button = None
        self._cancel_button = None
        self._status_block = None
