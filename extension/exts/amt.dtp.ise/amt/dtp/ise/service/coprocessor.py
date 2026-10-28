from typing import Optional

from isaacsim.util.debug_draw import _debug_draw
import omni
import omni.usd
import pxr
import pxr.Usd

from .core import Service
from .config import ConfigurationService
from ..coprocessor.app import CoProcessorApplication


class CoProcessorService(Service):
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

        self._application: Optional[CoProcessorApplication] = None

    @property
    def application(self) -> Optional[CoProcessorApplication]:
        return self._application
        
    def _service_start(self):
        if ConfigurationService().configuration.autostart_coprocessor:
            self._application = CoProcessorApplication()
            self._application.start()
    
    def _service_stop(self):
        if self._application is not None:
            self._application.stop()
            self._application = None

        self._singleton = None
        self._singleton_intialized = False
