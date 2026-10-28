from abc import ABC, abstractmethod
import inspect
import traceback
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any


class LogLevel:

    @property
    def color(self) -> str:
        return self._color

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_name(self) -> str:
        return self._short_name

    @property
    def value(self) -> int:
        return self._value

    def __init__(
            self,
            value: int,
            name: str,
            short_name: str,
            color: str,
            ):
        if not isinstance(value, int):
            raise TypeError("value must be an int")
        self._value = value

        if not isinstance(name, str):
            raise TypeError("name must be a str")
        self._name = name

        if not isinstance(short_name, str):
            raise TypeError("short_name must be a str")
        self._short_name = short_name

        if not isinstance(color, str):
            raise TypeError("color must be a str")
        self._color = color


class LogLevels:
    Trace = LogLevel(
        10, 
        "Trace",
        "TRA",
        "\033[1;36m", # Light Cyan
    )
    Debug = LogLevel(
        20, 
        "Debug",
        "DEB",
        "\033[1;34m", # Light Blue
    )
    Info = LogLevel(
        30, 
        "Info",
        "INF",
        "\033[0;92m", # Light Green
    )
    Warning = LogLevel(
        40, 
        "Warning",
        "WAR",
        "\033[1;93m", # Light Yellow
    )
    Error = LogLevel(
        50, 
        "Error",
        "ERR",
        "\033[0;91m", # Light Red
    )
    Critical = LogLevel(
        60, 
        "Critical",
        "CRI",
        "\033[1;35m", # Light Purple
    )

    @staticmethod
    def get(name: str) -> LogLevel:
        if not isinstance(name, str):
            raise TypeError("name must be a str")

        for log_level in [
            LogLevels.Trace,
            LogLevels.Debug,
            LogLevels.Info,
            LogLevels.Warning,
            LogLevels.Error,
            LogLevels.Critical,
        ]:
            if name.lower() == log_level.name.lower():
                return log_level
        raise Exception(f"Unknown log level: {name}")


class LogEntry:

    @property
    def cls(self) -> str:
        return self._class

    @property
    def code_context(self) -> List[str]:
        return self._code_context

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def function_name(self) -> str:
        return self._function_name
    
    @property
    def level(self) -> LogLevel:
        return self._level

    @property
    def line_number(self) -> int:
        return self._line_number
    
    @property
    def message_template(self) -> str:
        return self._message_template
    
    @property
    def module(self) -> str:
        return self._module

    @property
    def fields(self) -> Dict[str, Any]:
        return self._fields
    
    @property
    def exception(self) -> Optional[Exception]:
        return self._exception
    
    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    def __init__(
        self,
        timestamp: datetime,
        level: LogLevel,
        message_template: str,
        frames: List[inspect.FrameInfo],
        fields: Dict[str, Any] = {},
        exception: Optional[Exception] = None,
    ):
        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        self._timestamp = timestamp

        if not isinstance(level, LogLevel):
            raise TypeError("level must be a LogLevel")
        self._level = level

        if not isinstance(message_template, str):
            raise TypeError("message_template must be a str")
        self._message_template = message_template

        if not isinstance(frames, list) or not all(isinstance(f, inspect.FrameInfo) for f in frames):
            raise TypeError("frames must be a List[FrameInfo]")
        self._file_path = frames[0].filename
        self._line_number = frames[0].lineno
        self._function_name = frames[0].function
        self._code_context = frames[0].code_context if frames[0].code_context else []
        self._module = "<unknown_module>"
        if hasattr(frames[0].frame, "f_globals"):
            self._module = frames[0].frame.f_globals.get("__name__", "<unknown_module>")
        self._class = "<unknown_class>"
        if hasattr(frames[0].frame, "f_locals"):
            if "self" in frames[0].frame.f_locals:
                cls = type(frames[0].frame.f_locals["self"])
                self._class = cls.__qualname__
            if "cls" in frames[0].frame.f_locals and isinstance(frames[0].f_locals["cls"], type):
                cls = type(frames[0].frame.f_locals["cls"])
                self._class = cls.__qualname__

        if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields.keys()):
            raise TypeError("fields must be a Dict[str, Any]")
        self._fields = fields

        if exception is not None and not isinstance(exception, Exception):
            raise TypeError("exception must be an Optional[Exception]")
        self._exception = exception


class Enricher(ABC):

    @abstractmethod
    def enrich(self, log_entry: LogEntry) -> None:
        pass


class Sink(ABC):

    def __init__(self, level: Optional[LogLevel] = LogLevels.Trace):
        super().__init__()
        if level is None:
            level = LogLevels.Trace
        if not isinstance(level, LogLevel):
            raise TypeError("level must be a LogLevel")
        self._level = level

    @property
    def level(self) -> LogLevel:
        return self._level
    
    @level.setter
    def level(self, value) -> None:
        if not isinstance(value, LogLevel):
            raise TypeError("level must be a LogLevel")
        self._level = value

    @abstractmethod
    def receive(self, log_entry: LogEntry) -> None:
        pass


class ConsoleSink(Sink):

    def receive(self, log_entry: LogEntry) -> None:
        if not isinstance(log_entry, LogEntry):
            raise TypeError("log_entry must be a LogEntry")
        
        if log_entry.level.value < self.level.value:
            return

        clear_color = "\033[0m"
        #prefix = f"{log_entry.module}.{log_entry._class}.{log_entry.function_name} [{log_entry.level.color}{log_entry.level.short_name}{clear_color}] "
        #if len(prefix) > 100:
        date_time = log_entry.timestamp.astimezone().replace(tzinfo=None)
        timestamp = f"{date_time.strftime('%Y-%m-%d %I:%M:%S.%f')[:-3]} {date_time.strftime('%p')}"
        log_level = f"{log_entry.level.color}{log_entry.level.short_name}{clear_color}"
        prefix = f"{timestamp} [{log_level}] {log_entry._class}.{log_entry.function_name}: "
        body = f"{log_entry.message_template}"
        suffix = ""
        if log_entry.exception is not None:
            suffix = f"\nException: {log_entry.exception}\n{traceback.format_exc()}"
        print(f"{prefix}{body}{suffix}")


class Logger:

    @property
    def sinks(self) -> List[Sink]:
        return self._sinks

    def __init__(self, sinks: List[Sink], enrichers: Optional[List[Enricher]] = None, level: Optional[LogLevel] = None):
        if not isinstance(sinks, list) or not all(isinstance(s, Sink) for s in sinks):
            raise TypeError("sinks must be a List[Sink]")
        if len(sinks) == 0:
            raise ValueError("sinks must contain at least one Sink")
        self._sinks = sinks

        if enrichers is None:
            self._enrichers: List[Enricher] = []
        else:
            if not isinstance(enrichers, list) or not all(isinstance(e, Enricher) for e in enrichers):
                raise TypeError("enrichers must be a List[Enricher]")
            self._enrichers = enrichers

        if level is None:
            level = LogLevels.Trace
            if not isinstance(level, LogLevel):
                raise TypeError("level must be a LogLevel")
        self._level = level

    @property
    def level(self) -> LogLevel:
        return self._level
    
    @level.setter
    def level(self, value) -> None:
        if not isinstance(value, LogLevel):
            raise TypeError("level must be a LogLevel")
        self._level = value
    

    def _log(self, log_entry: LogEntry) -> None:
        if not isinstance(log_entry, LogEntry):
            raise TypeError("log_entry must be a LogEntry")
        
        if self.level is not None:
            if log_entry.level.value < self.level.value:
                return

        for enricher in self._enrichers:
            enricher.enrich(log_entry)

        for sink in self._sinks:
            sink.receive(log_entry)

    
    def trace(self, message_template: str, exception: Exception = None, **kwargs) -> None:
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the current frame so frames begin in the function that called this function
        self._log(LogEntry(timestamp, LogLevels.Trace, message_template, frames, kwargs, exception))

    def debug(self, message_template: str, exception: Exception = None, **kwargs) -> None:
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the current frame so frames begin in the function that called this function
        self._log(LogEntry(timestamp, LogLevels.Debug, message_template, frames, kwargs, exception))

    def info(self, message_template: str, exception: Exception = None, **kwargs) -> None:
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the current frame so frames begin in the function that called this function
        self._log(LogEntry(timestamp, LogLevels.Info, message_template, frames, kwargs, exception))

    def warning(self, message_template: str, exception: Exception = None, **kwargs) -> None:
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the current frame so frames begin in the function that called this function
        self._log(LogEntry(timestamp, LogLevels.Warning, message_template, frames, kwargs, exception))

    def error(self, message_template: str, *, exception: Exception = None, **kwargs) -> None:
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the current frame so frames begin in the function that called this function
        self._log(LogEntry(timestamp, LogLevels.Error, message_template, frames, kwargs, exception))

    def critical(self, message_template: str, *, exception: Exception = None, **kwargs) -> None:
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the current frame so frames begin in the function that called this function
        self._log(LogEntry(timestamp, LogLevels.Critical, message_template, frames, kwargs, exception))


class LoggerSink(Sink):

    def __init__(self, logger: Logger, level = LogLevels.Trace):
        super().__init__(level)
        if not isinstance(logger, Logger):
            raise TypeError("logger must be a Logger")
        self._logger = logger

    def receive(self, log_entry: LogEntry) -> None:
        if not isinstance(log_entry, LogEntry):
            raise TypeError("log_entry must be a LogEntry")
        self._logger._log(log_entry)


class _GlobalLogger(Logger):
    """
    A global logger that writes only to the console if no sinks are added.
    """

    def __init__(self):
        # Intentionally don't call super().__init__()
        self._sinks = []
        self._enrichers = []
        self._level = None
        self._no_sinks_sinks: List[Sink] = [ConsoleSink(LogLevels.Warning)]

    @property
    def no_sinks_sinks(self) -> List[Sink]:
        return self._no_sinks_sinks

    def _log(self, log_entry: LogEntry) -> None:
        if len(self.sinks) == 0:
            if len(self._no_sinks_sinks) == 0:
                return # Do nothing
            self._sinks.extend(self._no_sinks_sinks)
            super()._log(log_entry)
            self._sinks.clear()
        else:
            super()._log(log_entry)

#_default_logger = Logger([ConsoleSink(LogLevels.Warning)], [])
_logger = _GlobalLogger()