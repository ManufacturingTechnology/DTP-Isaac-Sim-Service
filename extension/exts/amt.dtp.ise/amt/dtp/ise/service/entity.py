import asyncio
import os
import pathlib
from typing import List, Optional, Callable, Dict

import isaacsim
import omni
import omni.kit.app
import omni.usd
import omni.ui
import pxr
import pxr.Usd

#from ..entity.__core import AssetBrowser, get_prims_recursive, get_prim_source_path
from ..entity.core import get_entity_instancer
from ..entity.behavior import BehaviorModel, Behavior, ModuleLoader, BehaviorCache
#from ..entity.__behavior import Behavior, BehaviorDataEntry, ModuleLoader
#from ..entity.__waypoint import Waypoint, WaypointDataEntry
from ..entity.waypoint import WaypointCache
from ..entity.workpiece import _workpiece_instancer, WorkpieceCache
#from ..entity.__workpiece import Workpiece, WorkpieceReference, WorkpieceInstanceDataEntry
from .core import Service
from .subscription import SubscriptionService, SubscriptionSet
from ..profiling import _zone
from ..isaac5_compatibility import PhysicsViewUnavailable
from ..lib.logging import _logger
from ..ui.core import MessageWindow
from .config import ConfigurationService


class EntityService(Service):
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

        self._entity_instancer = get_entity_instancer()
        self._behavior_cache: BehaviorCache = BehaviorCache()
        self._waypoint_cache: WaypointCache = WaypointCache()
        self._workpiece_cache: WorkpieceCache = WorkpieceCache()
        
        self._sub: Optional[SubscriptionSet] = None
        #self._is_playing = False

    @property
    def behavior_cache(self) -> BehaviorCache:
        return self._behavior_cache

    @property
    def waypoint_cache(self) -> WaypointCache:
        return self._waypoint_cache
    
    @property
    def workpiece_cache(self) -> WorkpieceCache:
        return self._workpiece_cache

    def _service_start(self) -> None:
        self._sub = SubscriptionService().create_subscription_set(EntityService.__name__)
        self._sub.on_timeline_play = self._on_play
        self._sub.on_timeline_pause = self._on_pause
        self._sub.on_timeline_stop = self._on_stop
        self._sub.on_timeline_current_time_ticked = self._on_update
        self._sub.on_timeline_current_time_ticked_permanent = self._on_timeline_current_time_ticked_permanent
        self._sub.on_physics_step = self._on_physics

        self._sub.enable()

    def _service_stop(self) -> None:

        self._waypoint_cache = None
        self._workpiece_cache = None
        self._workpiece_repository = None

        if self._sub is not None:
            self._sub.destroy()
            self._sub = None
        
        self._singleton = None
        self._singleton_intialized = False

    def _on_play(self) -> None:
        self._behavior_cache.build()
        self._waypoint_cache.build()
        self._workpiece_cache.build()
        self._entity_instancer.reset()
        self._sub.on_timeline_current_time_ticked_permanent = None

        if ConfigurationService().configuration.hide_waypoints_on_play:
            for waypoint in self._waypoint_cache.get_all():
                waypoint.visible = False

        for behavior in self._behavior_cache.get_all():
            for runner in behavior.runners:
                runner.play()

    def _on_pause(self) -> None:
        for behavior in self._behavior_cache.get_all():
            for runner in behavior.runners:
                runner.pause()

    def _on_stop(self) -> None:
        for behavior in self._behavior_cache.get_all():
            for runner in behavior.runners:
                runner.stop()

        if ConfigurationService().configuration.hide_waypoints_on_play:
            for waypoint in self._waypoint_cache.get_all():
                waypoint.visible = True

        self._behavior_cache.clear()
        self._waypoint_cache.clear()
        self._entity_instancer.reset()
        _workpiece_instancer.reset()
        self._workpiece_cache.clear()
        self._sub.on_timeline_current_time_ticked_permanent = self._on_timeline_current_time_ticked_permanent

    def _on_update(self, current_time: float, delta_time: float) -> None:
        # Outer aggregate zone: total time across all behavior updates.
        with _zone("behavior.update", group="behavior.update"):
            for behavior in self._behavior_cache.get_all():
                path = str(behavior.prim.GetPath())
                # group= makes each behavior a distinct Tracy source location -> its own
                # Statistics row (per-instance), filterable by "behavior.update:".
                # location= points the row at the behavior's actual script file.
                with _zone(
                    path,
                    group=f"behavior.update:{path}",
                    location=behavior.initialized_script_path or "",
                ):
                    try:
                        for runner in behavior.runners:
                            runner.update(current_time, delta_time)
                    except PhysicsViewUnavailable as ex:
                        # Physics tensor views are transiently unavailable while the sim
                        # view is rebuilt (e.g. a rigid body spawned/removed this frame).
                        # Skip this behavior's tick; it recovers once physics re-inits.
                        _logger.debug(f"Skipping behavior update (physics view unavailable): {path}", exception=ex)

    def _on_timeline_current_time_ticked_permanent(self, *args) -> None:
        # Check and reload modules

        # Don't hot reload modules while playing
        if omni.timeline.get_timeline_interface().is_stopped():
            # Stopped, not playing
            # Hot reload modified modules
            modified_modules = ModuleLoader.get_modified_modules()
            if modified_modules is not None:
                #self._destroy()
                self._behavior_cache.clear()
                for modified_module in modified_modules:
                    _logger.debug(f"Reloading {modified_module[0]}...")
                    ModuleLoader.load(*modified_module)
                    _logger.debug(f"Reloaded {modified_module[0]}")
                #self._initialize()
                self._behavior_cache.build()

    def _on_physics(self, current_time: float, delta_time: float) -> None:
        # Outer aggregate zone: total time across all behavior physics ticks.
        with _zone("behavior.physics", group="behavior.physics"):
            for behavior in self._behavior_cache.get_all():
                path = str(behavior.prim.GetPath())
                with _zone(
                    path,
                    group=f"behavior.physics:{path}",
                    location=behavior.initialized_script_path or "",
                ):
                    try:
                        for runner in behavior.runners:
                            runner.physics(current_time, delta_time)
                    except PhysicsViewUnavailable as ex:
                        # See _on_update: transient physics-view unavailability during a
                        # sim-view rebuild; skip this behavior's physics tick.
                        _logger.debug(f"Skipping behavior physics (physics view unavailable): {path}", exception=ex)
