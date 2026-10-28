import asyncio
import sys
import traceback
from typing import List, Optional, Type

#from isaacsim.util.debug_draw import _debug_draw
import omni
import omni.ext
import omni.timeline

from .__version__ import __version__
from .lib.control.codegen import set_generator
from .lib.logging import _logger, ConsoleSink, LogLevels
from .service.api import ApiService
from .service.entity import EntityService
from .service.kinematics import KinematicsService
from .service.coprocessor import CoProcessorService
from .service.config import ConfigurationService
from .service.core import Service
from .service.draw import DebugDrawService
from .service.logging import LoggingService, IsaacSink
from .service.menus import MenusService
from .service.spacemouse import SpacemouseService
from .service.subscription import SubscriptionService


_instance: Optional["AmtDtpExtension"] = None

def get_amt_dtp_extension_instance() -> "AmtDtpExtension":
    global _instance
    if _instance is None:
        raise ValueError("AmtDtpExtension instance is not initialized")
    return _instance

# Generated client modules (GET .../models.py) name the extension and its version in their header
set_generator("AMT DTP Isaac Sim Extension (ISE)", __version__)

# Configure temporary sinks until logging is configured
_logger.no_sinks_sinks.clear()
_logger.no_sinks_sinks.extend([IsaacSink(LogLevels.Trace), ConsoleSink(LogLevels.Warning)])

class AmtDtpExtension(omni.ext.IExt):

    def on_startup(self, ext_id: str):
        if not isinstance(ext_id, str):
            _logger.critical("Isaac Sim specified an invalid ext_id when starting extension.")
            raise TypeError("ext_id must be a str")
        self._ext_id = ext_id

        # # Clear global logger sinks and add Isaac sim sink
        # _logger.sinks.clear()
        # self._isaac_logger_sink = IsaacSink()
        # _logger.sinks.append(self._isaac_logger_sink)

        _logger.trace("AMT DTP ISE Startup Begin")

        service_types: List[Type[Service]] = [
            SubscriptionService,
            ConfigurationService, # Depends on SubscriptionFactoryService
            LoggingService, # Depends on ConfigurationService
            DebugDrawService, # Depends on ConfigurationService
            MenusService,
            EntityService,
            CoProcessorService, # Depends on ConfigurationService
            ApiService, # Depends on SubscriptionFactoryService, CoProcessorService
            KinematicsService, # Depends on ConfigurationService (port 8014 by default)
            SpacemouseService,
        ]

        self._services: List[Service] = []
        for service_type in service_types:
            _logger.trace(f"Initializing {service_type.__name__} service...")
            try:
                self._services.append(service_type().service_initialize(self._ext_id))
            except Exception as ex:
                try:
                    _logger.critical(f"Exception initializing {service_type.__name__} service", exception=ex)
                except Exception as ex2:
                    print(f"Exception logging while initializing {service_type.__name__} service: {ex2}", file=sys.stderr)
                    print(f"Exception initializing {service_type.__name__} service: {ex}", file=sys.stderr)
                    traceback.print_stack(file=sys.stderr)
                raise ex
            _logger.trace(f"Initialized {service_type.__name__} service")

        # Attempt to start each service sequentially. If a service throws an 
        #   exception, destroy all services then re-raise the exception.
        try:
            for service in self._services:
                _logger.trace(f"Starting {service.__class__.__name__} service...")
                service.service_start()
                _logger.trace(f"Started {service.__class__.__name__} service")
        except Exception as ex:
            try:
                _logger.critical(f"Exception starting {service.__class__.__name__} service", exception=ex)
            except Exception as ex2:
                print(f"Exception logging while starting {service.__class__.__name__} service: {ex2}", file=sys.stderr)
                print(f"Exception starting {service.__class__.__name__} service: {ex}", file=sys.stderr)
                traceback.print_stack(file=sys.stderr)
            self._destroy()
            raise ex
        
        # Set instance so other extensions have access to it
        global _instance
        _instance = self

        _logger.trace("AMT DTP ISE Startup Complete")

    def on_shutdown(self):
        _logger.trace("AMT DTP ISE Shutdown Begin")
        # If playing, try to stop it so that everything unloads/cleans up before shutting down

        # When attempting to stop during shutdown, the on_stop event may not fire before on_destroy,
        #   so manually trigger it just in case
        try:
            if omni.timeline.get_timeline_interface().is_playing():
                SubscriptionService()._trigger_timeline_stop()
        except Exception as ex:
            _logger.error("Error stopping playing simulation", exception=ex)

        # Make a best effort to stop if currently playing
        try:
            timeline = omni.timeline.get_timeline_interface()
            if timeline.is_playing():
                timeline.stop()
        except Exception as ex:
            _logger.error("Error stopping playing simulation", exception=ex)

        self._destroy()

        _logger.trace("AMT DTP ISE Shutdown Complete")

    def _destroy(self):
        # Copy and reverse so services are stopped in the reversed order
        reversed_services = list(self._services)
        reversed_services.reverse()
        for service in reversed_services:
            _logger.trace(f"Stopping {service.__class__.__name__} service...")
            try:
                service.service_stop()
            except Exception as ex:
                try:
                    _logger.error("Exception stopping service", exception=ex)
                except Exception as ex2:
                    print(f"Exception logging while stopping service: {ex2}", file=sys.stderr)
                    print(f"Exception stopping service: {ex}", file=sys.stderr)
                    traceback.print_stack(file=sys.stderr)
                    continue
            _logger.trace(f"Stopped {service.__class__.__name__} service")

        global _instance
        _instance = None
