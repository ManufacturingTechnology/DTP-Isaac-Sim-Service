"""Lightweight profiling helpers that feed Kit's active profiler backend (e.g. Tracy).

Zones opened here appear as named entries in the profiler flame graph, so you can
attribute frame time to your own code distinctly from the engine's instrumented zones.
Overhead is negligible when no profiler is capturing.

This is a *leaf* module on purpose: it has no project imports, so any layer (including
the service layer) can import it at module top without circular-import issues.

Public API for testbed builders (use inside ``BehaviorScript`` code):

- :func:`zone` — context manager to profile a *block*::

      from amt.dtp.ise.profiling import zone

      with zone("inverse_kinematics"):
          ...  # profiled block

- :func:`profile` — decorator to profile a whole *function/method*::

      from amt.dtp.ise.profiling import profile

      @profile
      def on_update(self, current_time, delta_time):
          ...

- :func:`profile_all` — class decorator that applies :func:`profile` to every method
  defined on a ``BehaviorScript`` subclass at once::

      @profile_all
      class Mill(BehaviorScript[MillConfig]):
          ...

All auto-fill the Tracy source location and group the zone under ``behavior.zone.<name>``
(filter the profiler by ``behavior.zone.`` to see just these). All short-circuit cheaply
when no profiler is capturing, so they're safe to leave on in normal runs.

:func:`_zone` is the extension-internal primitive with full control over the Tracy
``group`` (Statistics grouping key) and ``location``. Not intended for testbed code.
"""

import asyncio
import functools
import sys
from contextlib import contextmanager, nullcontext
from typing import Iterator, Optional

import carb.profiler

# carb.profiler zones are keyed by a channel mask; 1 is the conventional default.
_MASK = 1


@contextmanager
def _zone(
    name: str,
    *,
    group: Optional[str] = None,
    location: Optional[str] = None,
    lineno: int = 0,
) -> Iterator[None]:
    """Extension-internal profiler-zone primitive (testbed builders want :func:`zone`).

    ``carb.profiler.begin()`` auto-captures the *caller's* function/file as the zone's
    source location, and Tracy's Statistics window groups by source location — so every
    zone opened from this helper would otherwise collapse into a single ``_zone`` row.

    Pass ``group`` to override that source-location identity: Tracy then shows/groups by
    ``group``, giving one Statistics row per distinct ``group`` value. Keep ``group``
    drawn from a *bounded, stable* set (e.g. a prim path) — a new string every frame
    would leak source locations.

    Pass ``location`` (a file path) and ``lineno`` to set the zone's displayed source
    location, e.g. the call site, instead of the default ``Py::code``.
    """
    if not carb.profiler.is_profiler_active():
        # No active capture: skip all begin/end work, just run the wrapped block. Keeps
        # always-on instrumentation cheap in normal (non-profiling) runs.
        yield
        return
    if group is None and location is None:
        carb.profiler.begin(_MASK, name)
    else:
        carb.profiler.begin_with_location(
            _MASK, name, function=(group or name), filepath=(location or ""), lineno=lineno
        )
    try:
        yield
    finally:
        carb.profiler.end(_MASK)


def zone(name: str):
    """Public profiler zone for testbed builders to use inside ``BehaviorScript`` code.

    Wrap any block to measure it; it shows up in Tracy grouped under
    ``behavior.zone.<name>`` (filter the Statistics window by ``behavior.zone.`` to see
    just these), with the source location pointing at the call site::

        with zone("my_expensive_step"):
            ...

    Only ``name`` is required — the Tracy group and the source location (the file and
    line where ``zone()`` was called) are filled in automatically.

    Implementation note: the call site is captured via ``sys._getframe`` (a single,
    cheap frame access — the same mechanism ``carb.profiler.begin`` uses internally; do
    NOT switch to ``inspect.stack``, which walks/source-reads the whole stack). When no
    profiler is capturing, this short-circuits to a no-op context manager, so even the
    frame lookup is skipped.
    """
    if not carb.profiler.is_profiler_active():
        return nullcontext()
    frame = sys._getframe(1)  # frame 0 = this function; frame 1 = the caller (testbed code)
    return _zone(
        name,
        group=f"behavior.zone.{name}",
        location=frame.f_code.co_filename,
        lineno=frame.f_lineno,
    )


def profile(func=None, *, name: Optional[str] = None):
    """Decorator that profiles a function/method for testbed builders.

    Wrap any function so each call shows up in Tracy, grouped under
    ``behavior.zone.<name>`` with the source location pointing at the function's
    definition. Supports all three forms::

        @profile                         # name defaults to the qualified function name
        def on_update(self, t, dt): ...

        @profile("pick_part")            # explicit name
        def step(self): ...

        @profile(name="pick_part")       # explicit name (keyword)
        def step(self): ...

    Unlike :func:`zone`, this needs **no per-call reflection**: the source location is
    read once from the function object (``__code__``) at decoration time. The default
    name uses ``__qualname__`` (e.g. ``Mill.on_update``) so methods of the same name on
    different classes don't collide. Works on both sync and ``async def`` functions.

    When no profiler is capturing, the wrapper short-circuits to a direct call (one cheap
    ``is_profiler_active()`` check), so leaving it on in normal runs is effectively free.
    """
    # Allow @profile, @profile("name") and @profile(name="name").
    if isinstance(func, str):
        name = func
        func = None

    def decorator(fn):
        # Idempotent: never double-wrap something already profiled (e.g. an explicit
        # @profile under @profile_all, or a stray double @profile). The first wrapper's
        # name wins.
        if getattr(fn, "__amt_profiled__", False):
            return fn
        zone_name = name or fn.__qualname__
        group = f"behavior.zone.{zone_name}"
        # Captured once at decoration time — no runtime frame lookup.
        location = fn.__code__.co_filename
        lineno = fn.__code__.co_firstlineno

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                if not carb.profiler.is_profiler_active():
                    return await fn(*args, **kwargs)
                with _zone(zone_name, group=group, location=location, lineno=lineno):
                    return await fn(*args, **kwargs)
            async_wrapper.__amt_profiled__ = True
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not carb.profiler.is_profiler_active():
                return fn(*args, **kwargs)
            with _zone(zone_name, group=group, location=location, lineno=lineno):
                return fn(*args, **kwargs)
        wrapper.__amt_profiled__ = True
        return wrapper

    return decorator(func) if func is not None else decorator


def profile_all(cls):
    """Class decorator: apply :func:`profile` to every method defined *directly* on a
    ``BehaviorScript`` subclass — so testbed builders can instrument a whole script with
    one line instead of decorating each method::

        @profile_all
        class Mill(BehaviorScript[MillConfig]):
            def on_update(self, t, dt): ...   # -> behavior.zone.Mill.on_update
            def _pick_part(self): ...         # -> behavior.zone.Mill._pick_part

    Only plain functions defined on this class are wrapped. Left untouched: inherited
    methods, dunders, ``@staticmethod`` / ``@classmethod`` / ``@property``, nested
    classes, and methods already decorated with :func:`profile` (idempotent).

    Cost: with the ``is_profiler_active()`` short-circuit, each wrapped call costs ~one
    bool check when not capturing — fine for per-frame methods. ⚠️ Avoid it on classes
    with an *ultra-hot* helper (called thousands of times per frame): even a guarded zone
    adds up at that rate; decorate selectively there, or strip instrumentation.
    """
    for attr_name, attr in list(cls.__dict__.items()):
        if attr_name.startswith("__"):
            continue
        if isinstance(attr, (staticmethod, classmethod, property, type)):
            continue
        if not callable(attr):
            continue
        if getattr(attr, "__amt_profiled__", False):
            continue
        setattr(cls, attr_name, profile(attr))
    return cls
