from typing import List, Optional, Tuple

from isaacsim.util.debug_draw import _debug_draw
import numpy as np
import omni
import omni.usd
import pxr
import pxr.Usd

from .core import Service
from .subscription import SubscriptionService, SubscriptionSet
from .config import ConfigurationService

class DebugDrawService(Service):
    _singleton: Optional[Service] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True

        self._sub: Optional[SubscriptionSet] = None
        self._stage_is_open = False
        self._debug_draw_interface = None  # isaacsim.util.debug_draw acquire_debug_draw_interface() result
        self._draw_enabled = True

    def get_enabled(self) -> bool:
        return self._draw_enabled

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._draw_enabled = enabled

    def draw_line(
        self,
        start_position: Tuple[float, float, float] | List[float] | np.ndarray,
        end_position: Tuple[float, float, float] | List[float] | np.ndarray,
        color: Tuple[float, float, float, float] | List[float] | np.ndarray, # RGBA
        size: float = 2.0,
    ) -> None:
        self.draw_lines([start_position], [end_position], [color], [size])

    def draw_lines(
        self,
        start_positions: List[Tuple[float, float, float]] | List[List[float]] | List[np.ndarray],
        end_positions: List[Tuple[float, float, float]] | List[List[float]] | List[np.ndarray],
        colors: List[Tuple[float, float, float, float]] | List[List[float]] | List[np.ndarray], # RGBA
        sizes: List[float],
    ) -> None:
        if not isinstance(start_positions, list) or not all(isinstance(p, (tuple, list, np.ndarray)) for p in start_positions):
            raise TypeError("start_positions must be a List[Tuple[float, float, float]] | List[List[float]] | List[np.ndarray]")
        if not isinstance(end_positions, list) or not all(isinstance(p, (tuple, list, np.ndarray)) for p in end_positions):
            raise TypeError("end_positions must be a List[Tuple[float, float, float]] | List[List[float]] | List[np.ndarray]")
        if not isinstance(colors, list) or not all(isinstance(c, (tuple, list, np.ndarray)) for c in colors):
            raise TypeError("colors must be a List[Tuple[float, float, float, float]] | List[List[float]] | List[np.ndarray]")
        if not isinstance(sizes, list) or not all(isinstance(s, float) for s in sizes):
            raise TypeError("sizes must be a List[float]")
        # if not self._draw_enabled:
        #     return
        if not ConfigurationService().configuration.debug_draw_enabled:
            return
        self._debug_draw_interface.draw_lines(start_positions, end_positions, colors, sizes)

    def draw_point(
        self,
        position: Tuple[float, float, float] | List[float] | np.ndarray,
        color: Tuple[float, float, float, float] | List[float] | np.ndarray,
        size: float = 2.0,
    ) -> None:
        self.draw_points([position], [color], [size])

    def draw_points(
        self,
        positions: List[Tuple[float, float, float]] | List[List[float]] | List[np.ndarray],
        colors: List[Tuple[float, float, float, float]] | List[List[float]] | List[np.ndarray], # ARGB
        sizes: List[float], # Radius
    ) -> None:
        if not isinstance(positions, list) or not all(isinstance(p, (tuple, list, np.ndarray)) for p in positions):
            raise TypeError("positions must be a List[Tuple[float, float, float]] | List[List[float]] | List[np.ndarray]")
        if not isinstance(colors, list) or not all(isinstance(c, (tuple, list, np.ndarray)) for c in colors):
            raise TypeError("colors must be a List[Tuple[float, float, float, float]] | List[List[float]] | List[np.ndarray]")
        if not isinstance(sizes, list) or not all(isinstance(s, float) for s in sizes):
            raise TypeError("sizes must be a List[float]")
        # if not self._draw_enabled:
        #     return
        if not ConfigurationService().configuration.debug_draw_enabled:
            return
        self._debug_draw_interface.draw_points(positions, colors, sizes)
        
        
    def _service_start(self):
        self._sub = SubscriptionService().create_subscription_set(DebugDrawService.__name__)
        self._sub.on_timeline_current_time_ticked_permanent = self._debug_draw_clear
        self._sub.on_stage_closing = self._on_stage_closing
        self._sub.on_stage_opened = self._on_stage_opened
        self._sub.enable()

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is not None:
            self._stage_is_open = True

        self._draw_enabled = ConfigurationService().configuration.debug_draw_enabled
        self._debug_draw_interface = _debug_draw.acquire_debug_draw_interface()
    
    def _service_stop(self):
        if self._sub is not None:
            self._sub.destroy()

        self._debug_draw_interface = None

        self._singleton = None
        self._singleton_intialized = False

    def _on_stage_closing(self, *args):
        self._stage_is_open = False

    def _on_stage_opened(self, *args):
        self._stage_is_open = True

    def _debug_draw_clear(self, *args):
            # If this is called while a stage is not open, it can cause a segmentation fault
            #   which causes Isaac Sim to hard crash
            if self._stage_is_open:
                try:
                    if self._debug_draw_interface is None:
                        # This spams the isaac sim log, call it as rarely as possible
                        self._debug_draw_interface = _debug_draw.acquire_debug_draw_interface()
                    self._debug_draw_interface.clear_lines()
                    self._debug_draw_interface.clear_points()
                except Exception as ex:
                    pass # TODO: log
            else:
                self._debug_draw_interface = None

