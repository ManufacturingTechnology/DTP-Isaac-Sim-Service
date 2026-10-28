from abc import abstractmethod
import sys
import traceback
import numpy as np
import time
from typing import Callable, List, Dict, Optional, Tuple
from pydantic import BaseModel, Field

import carb.events._events
import omni.physx.bindings._physx
import omni.timeline
import omni.usd
import omni.usd._usd

from .core import Service
from ..lib.logging import _logger
from ..profiling import _zone
from ..ui.core import MessageWindow
#from ..service.logging import _logger


class SubscriptionSet:

    @property
    def destroyed(self) -> bool:
        return self._destroyed

    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @property
    def name(self) -> str:
        return self._name

    def __init__(self, subscription_factory: "SubscriptionService", name: str):
        if not isinstance(subscription_factory, SubscriptionService):
            raise TypeError("subscription_hub must be a SubscriptionHub")
        self._subscription_factory = subscription_factory

        if not isinstance(name, str):
            raise TypeError("name must be a str")
        self._name = name

        self._enabled = False
        self._destroyed = False

        self.on_timeline: Callable[[carb.events._events.IEvent], None] = None
        self.on_timeline_play: Callable[[None], None] = None
        self.on_timeline_pause: Callable[[None], None] = None
        self.on_timeline_stop: Callable[[None], None] = None
        self.on_timeline_current_time_changed: Callable[[float, float], None] = None

        """
        Ticks all the time, even if not playing.
        """
        self.on_timeline_current_time_ticked_permanent: Callable[[float, float], None] = None
        
        """
        Ticks only when playing.
        """
        self.on_timeline_current_time_ticked: Callable[[float, float], None] = None
        self.on_stage: Callable[[carb.events._events.IEvent], None] = None
        self.on_stage_opening: Callable[[None], None] = None
        self.on_stage_opened: Callable[[None], None] = None
        self.on_stage_closing: Callable[[None], None] = None
        self.on_stage_closed: Callable[[None], None] = None
        self.on_stage_saving: Callable[[None], None] = None
        self.on_stage_saved: Callable[[None], None] = None
        self.on_stage_selection_changed: Callable[[List[str]], None] = None
        self.on_stage_omnigraph_start: Callable[[None], None] = None
        self.on_stage_omnigraph_stop: Callable[[None], None] = None
        self.on_stage_simulation_start: Callable[[None], None] = None
        self.on_stage_simulation_stop: Callable[[None], None] = None
        self.on_stage_animation_start: Callable[[None], None] = None
        self.on_stage_animation_stop: Callable[[None], None] = None
        self.on_physics_step: Callable[[float, float], None] = None

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def destroy(self) -> None:
        self._enabled = False
        self._subscription_factory.destroy_subscription_set(self)


class PerformanceMonitor:
    _max_duration_count: int = 1_000

    def __init__(self, name: str):
        if not isinstance(name, str):
            raise TypeError("name must be a str")
        self._name = name

        self._durations: List[Tuple[float, float]] = []
        self._start_time: Optional[float] = None
    
    def get_durations(self) -> List[Tuple[float, float]]:
        return list(self._durations)

    def pop_durations(self) -> List[Tuple[float, float]]:
        """
        Get a list of times and durations that the monitor has recorded. Returns (time, duration).
        """
        items = self._durations
        self._durations = []
        return items

    def start(self) -> None:
        self._start_time = time.process_time()

    def stop(self) -> None:
        if self._start_time is None:
            return
        self._durations.append((time.process_time(), time.process_time() - self._start_time))
        self._start_time = None

        if len(self._durations) > self._max_duration_count:
            # Trim to self._max_duration_count size, keeping the most recent samples.
            self._durations = self._durations[-self._max_duration_count:]


class SubscriptionService(Service):
    _singleton: Optional[Service] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls, *args, **kwargs)
        return cls._singleton

    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True

        self._error: Optional[str] = None
        self._error_window: Optional[MessageWindow] = None

        self._subscription_handlers: List[SubscriptionHandler] = []

        self._timeline_subscription: carb.events._events.ISubscription = None
        self._stage_subscription: carb.events._events.ISubscription = None
        self._physics_subscription: carb._carb.Subscription = None
        
        self._duration_current_time_ticked = PerformanceMonitor(f"Total Ticked")
        self._duration_current_time_ticked_permanent = PerformanceMonitor(f"Total TickedPermanent")
        self._duration_physics = PerformanceMonitor(f"Total Physics")

        self._performance_monitors: List[PerformanceMonitor] = []

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def subscription_handlers(self) -> List["SubscriptionHandler"]:
        return self._subscription_handlers
    
    @property
    def performance_monitors(self) -> List[PerformanceMonitor]:
        return list(self._performance_monitors)

    def contains_subscription_set(self, subscription_set: SubscriptionSet) -> bool:
        return self._get_subscription_handler(subscription_set)

    def create_subscription_set(self, name: str) -> SubscriptionSet:
        subscription_set = SubscriptionSet(self, name)
        self._subscription_handlers.append(SubscriptionHandler(subscription_set))
        return subscription_set

    def destroy_subscription_set(self, subscription_set: SubscriptionSet) -> None:
        if not isinstance(subscription_set, SubscriptionSet):
            raise TypeError("subscription_set must be a SubscriptionSet")
        if subscription_set.destroyed:
            return
        
        target_handler = self._get_subscription_handler(subscription_set)
        if target_handler is None:
            raise Exception("Subscription handler not found for SubscriptionSet")
        
        self._destroy_subscription_handler(target_handler)

    def create_performance_monitor(self, name: str) -> PerformanceMonitor:
        if not isinstance(name, str):
            raise TypeError("name must be a str")
        monitor = PerformanceMonitor(name)
        self._performance_monitors.append(monitor)
        return monitor

    def _destroy_subscription_handler(self, subscription_handler: "SubscriptionHandler") -> None:
        subscription_handler.subscription_set._destroyed = True
        self._subscription_handlers.remove(subscription_handler)

    def _get_subscription_handler(self, subscription_set: SubscriptionSet) -> Optional["SubscriptionHandler"]:
        if not isinstance(subscription_set, SubscriptionSet):
            raise TypeError("subscription_set must be a SubscriptionSet")
        
        target_handler = None
        for handler in self._subscription_handlers:
            if handler.subscription_set == subscription_set:
                target_handler = handler
                break

        return target_handler

    def _service_start(self):
        usd_context: omni.usd._usd.UsdContext = omni.usd.get_context()
        
        # Timeline subscribe
        timeline_event_stream = omni.timeline.get_timeline_interface().get_timeline_event_stream()
        self._timeline_subscription = timeline_event_stream.create_subscription_to_pop(self._on_timeline)

        # Stage subscribe
        stage_event_stream: carb.events._events.IEventStream = usd_context.get_stage_event_stream()
        self._stage_subscription = stage_event_stream.create_subscription_to_pop(self._on_stage)
        
        # Physics subscribe
        physxIFace: omni.physx.bindings._physx.Physx = omni.physx.get_physx_interface()
        self._physics_subscription = physxIFace.subscribe_physics_step_events(self._on_physics)
        
    def _service_stop(self):
        if self._error_window is not None:
            self._error_window.hide()
            self._error_window = None

        for handler in list(self._subscription_handlers):
            self._destroy_subscription_handler(handler)

        # Timeline unsubscribe
        self._timeline_subscription = None

        # Stage unsubscribe
        self._stage_subscription = None

        # Physics unsubscribe
        self._physics_subscription = None

        self._singleton = None
        self._singleton_intialized = False

    def _trigger_timeline_stop(self):
        for handler in self._subscription_handlers:
            if handler.subscription_set.on_timeline_stop is not None:
                try:
                    handler.subscription_set.on_timeline_stop()
                except Exception as ex:
                    _logger.error("Error triggering timeline stop", exception=ex)

    def _on_timeline(self, event: carb.events._events.IEvent):
        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED):
            self._duration_current_time_ticked.start()
        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED_PERMANENT):
            self._duration_current_time_ticked_permanent.start()
        if event.type == int(omni.timeline.TimelineEventType.PLAY):
            self._error = None
            if self._error_window is not None:
                self._error_window.hide()
                self._error_window = None

        for handler in self._subscription_handlers:
            try:
                handler._on_timeline(event)
            except Exception as ex:
                _logger.error("Error executing on_timeline", exception=ex)
                if event.type == int(omni.timeline.TimelineEventType.PLAY):
                    # A handler errored while starting playback, so stop the timeline.
                    omni.timeline.get_timeline_interface().stop()
                    self._error = "An Error Occured When Starting:\n" + "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                    self._error_window = MessageWindow(
                        "Error Report", 
                        f"An error occured when attempting to play:\n{ex}",
                        ex
                    ).show()
                if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED):
                    # A handler errored while during on_update, so stop the timeline.
                    omni.timeline.get_timeline_interface().stop()
                    self._error = "An Error Occured While Running:\n" + "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                    self._error_window = MessageWindow(
                        "Error Report", 
                        f"An error occured while running:\n{ex}",
                        ex
                    ).show()

        if event.type == int(omni.timeline.TimelineEventType.STOP):
            self._performance_monitors.clear()
        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED):
            self._duration_current_time_ticked.stop()
        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED_PERMANENT):
            self._duration_current_time_ticked_permanent.stop()

    def _on_stage(self, event: carb.events._events.IEvent):
        for handler in self._subscription_handlers:
            try:
                handler._on_stage(event)
            except Exception as ex:
                _logger.error("Error executing on_stage", exception=ex)

    def _on_physics(self, delta_time: float):
        self._duration_physics.start()
        for handler in self._subscription_handlers:
            try:
                handler._on_physics(delta_time)
            except Exception as ex:
                _logger.error("Error executing on_physics", exception=ex)
        self._duration_physics.stop()


class SubscriptionHandler:

    @property
    def subscription_set(self) -> SubscriptionSet:
        return self._set

    def __init__(self, subscription_set: SubscriptionSet):
        if not isinstance(subscription_set, SubscriptionSet):
            raise Exception("subscriptions must be Subscriptions")
        self._set = subscription_set

        self._stage_time: float = 0.0
        self._ticked_monitor = PerformanceMonitor(f"{self._set.name}")
        self._ticked_permanent_monitor = PerformanceMonitor(f"{self._set.name}")
        self._physics_monitor = PerformanceMonitor(f"{self._set.name}")

    def get_ticked_monitor(self) -> PerformanceMonitor:
        return self._ticked_monitor
    
    def get_ticked_permanent_monitor(self) -> PerformanceMonitor:
        return self._ticked_permanent_monitor
    
    def get_physics_monitor(self) -> PerformanceMonitor:
        return self._physics_monitor

    def _on_timeline(self, event: carb.events._events.IEvent):
        if not self._set.enabled:
            return

        if self._set.on_timeline:
            self._set.on_timeline(event)

        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED):
            if self._set.on_timeline_current_time_ticked:
                current_time = event.payload["currentTime"]
                delta_time = event.payload["dt"]
                tick = event.payload["tick"]
                
                #current_time: float = omni.timeline.get_timeline_interface().get_current_time()
                #delta_time = current_time - self._stage_time
                # self._stage_time = current_time
                self._ticked_monitor.start()
                _zname = f"subscription.update.{self._set.name}"
                with _zone(_zname, group=_zname):
                    self._set.on_timeline_current_time_ticked(current_time, delta_time)
                self._ticked_monitor.stop()
            return
        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_CHANGED):
            if self._set.on_timeline_current_time_changed:
                current_time: float = omni.timeline.get_timeline_interface().get_current_time()
                delta_time = current_time - self._stage_time
                self._stage_time = current_time
                self._set.on_timeline_current_time_changed(current_time, delta_time)
            return
        if event.type == int(omni.timeline.TimelineEventType.CURRENT_TIME_TICKED_PERMANENT):
            if self._set.on_timeline_current_time_ticked_permanent:
                current_time = event.payload["currentTime"]
                delta_time = event.payload["dt"]
                tick = event.payload["tick"]
                self._ticked_permanent_monitor.start()
                _zname = f"subscription.tick.{self._set.name}"
                with _zone(_zname, group=_zname):
                    self._set.on_timeline_current_time_ticked_permanent(current_time, delta_time)
                self._ticked_permanent_monitor.stop()
            return
        if event.type == int(omni.timeline.TimelineEventType.PLAY):
            if self._set.on_timeline_play:
                self._set.on_timeline_play()
            return
        if event.type == int(omni.timeline.TimelineEventType.PAUSE):
            if self._set.on_timeline_pause:
                self._set.on_timeline_pause()
            return
        if event.type == int(omni.timeline.TimelineEventType.STOP):
            if self._set.on_timeline_stop:
                self._set.on_timeline_stop()
            return

    def _on_stage(self, event: carb.events._events.IEvent):
        if not self._set.enabled:
            return
        
        if self._set.on_stage:
            self._set.on_stage(event)
        if event.type == int(omni.usd.StageEventType.OPENING):
            if self._set.on_stage_opening:
                self._set.on_stage_opening()
        if event.type == int(omni.usd.StageEventType.OPENED):
            if self._set.on_stage_opened:
                self._set.on_stage_opened()
        if event.type == int(omni.usd.StageEventType.CLOSING):
            if self._set.on_stage_closing:
                self._set.on_stage_closing()
        if event.type == int(omni.usd.StageEventType.CLOSED):
            if self._set.on_stage_closed:
                self._set.on_stage_closed()
        if event.type == int(omni.usd.StageEventType.SAVING):
            if self._set.on_stage_saving:
                self._set.on_stage_saving()
        if event.type == int(omni.usd.StageEventType.SAVED):
            if self._set.on_stage_saved:
                self._set.on_stage_saved()
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            if self._set.on_stage_selection_changed:
                selection_paths: List[str] = omni.usd.get_context().get_selection().get_selected_prim_paths()
                self._set.on_stage_selection_changed(selection_paths)
        if event.type == int(omni.usd.StageEventType.OMNIGRAPH_START_PLAY):
            if self._set.on_stage_omnigraph_start:
                self._set.on_stage_omnigraph_start()
        if event.type == int(omni.usd.StageEventType.OMNIGRAPH_STOP_PLAY):
            if self._set.on_stage_omnigraph_stop:
                self._set.on_stage_omnigraph_stop()
        if event.type == int(omni.usd.StageEventType.SIMULATION_START_PLAY):
            if self._set.on_stage_simulation_start:
                self._set.on_stage_simulation_start()
        if event.type == int(omni.usd.StageEventType.SIMULATION_STOP_PLAY):
            if self._set.on_stage_simulation_stop:
                self._set.on_stage_simulation_stop()
        if event.type == int(omni.usd.StageEventType.ANIMATION_START_PLAY):
            if self._set.on_stage_animation_start:
                self._set.on_stage_animation_start()
        if event.type == int(omni.usd.StageEventType.ANIMATION_STOP_PLAY):
            if self._set.on_stage_animation_stop:
                self._set.on_stage_animation_stop()

    def _on_physics(self, delta_time: float):
        if not self._set.enabled:
            return
        
        if self._set.on_physics_step:
            current_time: float = float(omni.timeline.get_timeline_interface().get_current_time())
            self._physics_monitor.start()
            _zname = f"subscription.physics.{self._set.name}"
            with _zone(_zname, group=_zname):
                self._set.on_physics_step(current_time, delta_time)
            self._physics_monitor.stop()

