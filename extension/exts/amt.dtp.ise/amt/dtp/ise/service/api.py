from typing import Optional

from ..api.core import ApiController
from ..api.routers import routers
from .core import Service
from .subscription import SubscriptionService, SubscriptionSet
from ..lib.logging import _logger
#from .logging import _logger


class ApiService(Service):
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

        self._api_controller: Optional[ApiController] = None

    def _service_start(self) -> None:
        if self._api_controller is not None:
            raise Exception("_api_controller is not None. Can not start ApiService.")
        self._api_controller = ApiController()
        for router in routers:
            self._api_controller.add_router(router)
        self._api_controller.start()

    def _service_stop(self) -> None:
        if self._api_controller is not None:
            self._api_controller.stop()
            self._api_controller = None

        self._singleton = None
        self._singleton_intialized = False
