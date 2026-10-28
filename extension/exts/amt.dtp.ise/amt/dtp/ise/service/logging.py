from abc import abstractmethod
import inspect
import traceback
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any

import carb
from .core import Service
from .config import ConfigurationService
from ..lib.logging import *
from ..lib.logging import _logger as _global_logger


class IsaacSink(Sink):

    def receive(self, log_entry: LogEntry) -> None:
        if not isinstance(log_entry, LogEntry):
            raise TypeError("log_entry must be a LogEntry")
        
        if log_entry.level.value < self.level.value:
            return

        prefix = f"[{log_entry.level.short_name}] [{log_entry.module}.{log_entry._class}.{log_entry.function_name}] "
        body = f"{log_entry.message_template}"
        suffix = ""
        if log_entry.exception is not None:
            suffix = f"\nException: {log_entry.exception}\n{traceback.format_exc()}"
        log_line = f"{prefix}{body}{suffix}"

        match log_entry.level:
            case LogLevels.Trace:
                carb.log_verbose(log_line)
            case LogLevels.Debug:
                carb.log_verbose(log_line)
            case LogLevels.Info:
                carb.log_info(log_line)
            case LogLevels.Warning:
                carb.log_warn(log_line)
            case LogLevels.Error:
                carb.log_error(log_line)
            case LogLevels.Critical:
                carb.log_error(log_line)
            case _:
                raise Exception("Invalid LogLevel")


class LoggingService(Service):
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

        self._logger: Optional[Logger] = None
        self._logger_sink: Optional[LoggerSink] = None

    @property
    def logger(self) -> Optional[Logger]:
        return self._logger

    def _service_start(self):
        self._service_is_started = True
        
        # Create and configure the service logger
        config_level = LogLevels.get(ConfigurationService().configuration.log_level)
        self._logger = Logger([IsaacSink(LogLevels.Trace), ConsoleSink(config_level)],[])
        
        # Attach the service logger to the global logger as a sink,
        #   routing all global logger events to the service logger
        self._logger_sink = LoggerSink(self._logger)
        _global_logger.sinks.append(self._logger_sink)

    def _service_stop(self):
        self._singleton = None
        self._singleton_intialized = False

        # Detach the service logger from the global logger
        if self._logger_sink is not None:
            if self._logger_sink in _global_logger.sinks:
                _global_logger.sinks.remove(self._logger_sink)

        self._logger = None
        self._logger_sink = None
