from abc import ABC, abstractmethod
from typing import Optional

class Service(ABC):

    def __init__(self):
        super().__init__()
        self._service_is_started = False
        self._ext_id: Optional[str] = None

    def service_initialize(self, ext_id: str) -> "Service":
        if not isinstance(ext_id, str):
            raise TypeError("ext_id must be a str")
        self._ext_id = ext_id
        return self
    
    @property
    def service_is_started(self) -> bool:
        return self._service_is_started

    def service_start(self) -> None:
        if self._ext_id is None:
            raise Exception("Service has not been initialized and therefore can not start")
        self._service_start()
        self._service_is_started = True

    def service_stop(self) -> None:
        self._service_is_started = False
        self._service_stop()

    @abstractmethod
    def _service_start(self) -> None:
        pass

    @abstractmethod
    def _service_stop(self) -> None:
        pass
