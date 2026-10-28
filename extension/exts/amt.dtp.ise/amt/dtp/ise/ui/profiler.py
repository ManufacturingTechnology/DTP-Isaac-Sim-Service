import math
import time
from typing import List, Optional, Tuple

from amt.dtp.ise.service.entity import EntityService
import omni.ui

from .core import ExtensionWindow
from ..service.subscription import SubscriptionService, PerformanceMonitor





# Distinct line colors (0xAABBGGRR) cycled across the monitored series. There
# can be many series (e.g. one per behavior runner), so keep this list long to
# minimize color reuse.
_PALETTE: List[int] = [
    0xFF76B900,  # green
    0xFFF7C34F,  # amber
    0xFF7373E5,  # red
    0xFFF7B34F,  # orange
    0xFFC368BA,  # purple
    0xFFACB64D,  # teal
    0xFF4FC3F7,  # blue
    0xFF8087FF,  # pink
    0xFF4B19E6,  # crimson
    0xFFC88200,  # azure
    0xFF3082F5,  # tangerine
    0xFFB41E91,  # violet
    0xFFF0F046,  # cyan
    0xFFE632F0,  # magenta
    0xFF3CF5D2,  # lime
    0xFF808000,  # dark teal
    0xFF286EAA,  # brown
    0xFF19E1FF,  # yellow
    0xFFD4BEFA,  # rose
    0xFFAAAAAA,  # grey
    0xFFC3FFAA,  # mint
    0xFF5050FF,  # coral
    0xFFFFBEDC,  # lavender
    0xFF008080,  # olive
]


# Width of the legend's "Series" name column, in pixels. Wide enough for long
# behavior/runner names that would otherwise overflow into the value columns.
_NAME_COL_WIDTH: int = 480


def _resample_zero_gaps(
    samples: List[Tuple[float, float]], t0: float, t1: float, count: int
) -> List[float]:
    """Resample (timestamp, value) samples onto `count` evenly spaced grid times
    in [t0, t1]. Each grid bin holds the max value of any sample landing in it,
    and 0.0 where there is no data. Sparse, sporadically-sampled data then renders
    as visible zero-returning spikes rather than single invisible points."""
    if count < 2:
        count = 2
    grid = [0.0] * count
    if not samples or t1 <= t0:
        # Degenerate window: drop the most recent value at the right edge so it
        # still shows as a spike against the surrounding zeros.
        if samples:
            grid[-1] = max(v for _, v in samples)
        return grid

    step = (t1 - t0) / (count - 1)
    for ts, v in samples:
        k = round((ts - t0) / step)
        k = 0 if k < 0 else (count - 1 if k >= count else k)
        if v > grid[k]:
            grid[k] = v
    return grid


def _peak_indices(values: List[float], factor: float = 5.0) -> set:
    """Indices of 'peak' values: starting from the largest, a value is a peak if
    it exceeds `factor` times the largest of the remaining values. Iterates
    downward so several successively-smaller spikes can be caught, stopping at the
    first value that is within `factor` of the next."""
    n = len(values)
    if n < 2:
        return set()
    order = sorted(range(n), key=lambda i: values[i], reverse=True)
    peaks = set()
    for k in range(n - 1):
        cur = values[order[k]]
        nxt = values[order[k + 1]]
        if cur > factor * nxt:
            peaks.add(order[k])
        else:
            break
    return peaks


class _Series:
    """One monitor's line within the shared plot, plus its legend entry."""

    def __init__(self, monitor: PerformanceMonitor, color: int):
        self.monitor = monitor
        self.color = color
        # Raw (top) and moving-average (bottom) line widgets.
        self.plot: Optional[omni.ui.Plot] = None
        self.avg_plot: Optional[omni.ui.Plot] = None
        self.samples: List[Tuple[float, float]] = []
        # Moving-average samples, recomputed from self.samples each update.
        self.avg_samples: List[Tuple[float, float]] = []

    def pull_new_data(self) -> None:
        # Convert s to ms
        self.samples.extend([(t[0], t[1] * 1_000.0) for t in self.monitor.pop_durations()])

    def get_time_extents(self) -> Tuple[float, float]:
        if len(self.samples) == 0:
            return (1e100, -1.0)
        return (self.samples[0][0], self.samples[-1][0])

    def prune_old_data(self, cutoff_time: float) -> None:
        if not isinstance(cutoff_time, float):
            raise TypeError("cutoff_time must be a float")

        while self.samples and self.samples[0][0] < cutoff_time:
            self.samples.pop(0)

    def compute_moving_average(self, window_seconds: float, ignore_peaks: bool = False) -> None:
        # Trailing average over a sliding time window. Emit exactly one value per
        # raw sample (same timestamps) so the average line aligns point-for-point
        # with the raw line when both are stretched to the same plot width.
        self.avg_samples = []
        if not self.samples:
            return

        # Optionally drop large intermittent peaks so the average reflects steady
        # state rather than rare spikes. Excluded samples still occupy a timestamp
        # (we emit the window average at that point), they just don't contribute.
        peaks = _peak_indices([v for _, v in self.samples]) if ignore_peaks else set()

        first_t = self.samples[0][0]
        start = 0
        running_sum = 0.0
        running_count = 0
        total_sum = 0.0
        total_count = 0
        first_valid: Optional[float] = None
        warmup_count = 0
        for i, (t, v) in enumerate(self.samples):
            if i not in peaks:
                running_sum += v
                running_count += 1
                total_sum += v
                total_count += 1
            while self.samples[start][0] < t - window_seconds:
                if start not in peaks:
                    running_sum -= self.samples[start][1]
                    running_count -= 1
                start += 1
            if t - first_t < window_seconds:
                # Warm-up: not enough trailing history. The partial-window average
                # is skewed (often by the costly first tick), so emit a placeholder
                # and back-fill it below with a flat baseline.
                self.avg_samples.append((t, 0.0))
                warmup_count += 1
            else:
                avg = running_sum / running_count if running_count > 0 else 0.0
                if first_valid is None and running_count > 0:
                    first_valid = avg
                self.avg_samples.append((t, avg))

        # Back-fill the leading warm-up points with a flat, non-skewed baseline:
        # the first full-window average, or the overall mean if no full window yet.
        fill = first_valid if first_valid is not None else (total_sum / total_count if total_count else 0.0)
        for j in range(warmup_count):
            self.avg_samples[j] = (self.avg_samples[j][0], fill)

    def get_peak(self) -> float:
        if len(self.samples) == 0:
            return 0.0
        return max(self.samples, key=lambda x: x[1])[1]

    def get_avg_peak(self) -> float:
        if len(self.avg_samples) == 0:
            return 0.0
        return max(self.avg_samples, key=lambda x: x[1])[1]

    def set_raw_visible(self, visible: bool) -> None:
        # Toggle the raw line widget (top-N-by-max filter).
        if self.plot is not None:
            self.plot.visible = visible

    def set_avg_visible(self, visible: bool) -> None:
        # Toggle the moving-average line widget (top-N-by-average filter).
        if self.avg_plot is not None:
            self.avg_plot.visible = visible

    def plot_raw_data(self, scale_max: float, values: Optional[List[float]] = None) -> None:
        # values overrides the y-data (e.g. zero-order-hold resampled); otherwise
        # the raw sample values are plotted directly.
        ys = values if values is not None else [s[1] for s in self.samples]
        self._set_plot(self.plot, ys, scale_max)

    def plot_avg_data(self, scale_max: float, values: Optional[List[float]] = None) -> None:
        ys = values if values is not None else [s[1] for s in self.avg_samples]
        self._set_plot(self.avg_plot, ys, scale_max)

    @staticmethod
    def _set_plot(plot: Optional[omni.ui.Plot], ys: List[float], scale_max: float) -> None:
        if not isinstance(scale_max, float):
            raise TypeError("scale_max must be a float")

        # The plot widget only exists after the owning frame has been built.
        if plot is None:
            return

        plot.scale_min = 0.0
        plot.scale_max = scale_max
        if ys:
            plot.set_data(*ys)
        else:
            plot.set_data(0.0)


class PerformancePlotGroup:

    # Number of most-recent samples shown for each series.
    #_sample_window: int = 1_000
    # X-axis time window (seconds): default plus the adjustable slider bounds.
    _time_window_default: int = 120
    _time_window_min: int = 30
    _time_window_max: int = 600
    # Trailing moving-average window (seconds): default plus slider bounds.
    _average_window_default: int = 30
    _average_window_min: int = 2
    _average_window_max: int = 300
    # Height of each plotting area in pixels (there are two, stacked).
    _plot_height: int = 400
    # Number of y-axis tick marks (including 0 and the top of scale).
    _tick_count: int = 11
    # Grid resolution (points) used when zero-order-hold resampling is active.
    _zero_fill_resolution: int = 400
    # Zero-fill only applies to a series with fewer than this many points; series
    # with enough points already render fine as a normal line.
    _zero_fill_threshold: int = 5

    def __init__(self, title: str, zero_fill: bool = False):
        if not isinstance(title, str):
            raise TypeError("title must be a str")
        self._title = title

        if not isinstance(zero_fill, bool):
            raise TypeError("zero_fill must be a bool")

        self._monitors: List[PerformanceMonitor] = []
        self._series: List[_Series] = []
        # Y-axis tick labels for the raw (top) and moving-average (bottom) plots.
        self._tick_labels: List[omni.ui.Label] = []
        self._avg_tick_labels: List[omni.ui.Label] = []
        # Legend table rows (row HStack, swatch, name, value label) for each plot,
        # reassigned every update so they can be sorted/filtered without rebuilding.
        # Max legend ranks by raw max; average legend ranks by moving-average peak.
        self._max_legend_rows: List[
            Tuple[omni.ui.HStack, omni.ui.Rectangle, omni.ui.Label, omni.ui.Label]
        ] = []
        self._avg_legend_rows: List[
            Tuple[omni.ui.HStack, omni.ui.Rectangle, omni.ui.Label, omni.ui.Label]
        ] = []

        # Remembered expand/collapse state, preserved across window rebuilds.
        self._collapsed: bool = True
        # Inner plot frames: raw (top) expanded, moving average (bottom) collapsed.
        self._raw_collapsed: bool = False
        self._avg_collapsed: bool = False
        # Top-N filter: show only the N highest-max series (0 = show all).
        # Preserved across window rebuilds; the slider model is recreated each build.
        self._filter_count: int = 0
        self._filter_model: Optional[omni.ui.SimpleIntModel] = None
        # Live x-axis time window (seconds), adjustable via slider and persisted.
        self._time_window: float = float(self._time_window_default)
        self._time_window_model: Optional[omni.ui.SimpleIntModel] = None
        # Live moving-average window (seconds), adjustable via slider and persisted.
        self._average_window: float = float(self._average_window_default)
        self._average_window_model: Optional[omni.ui.SimpleIntModel] = None
        # Zero-fill: for sporadically-sampled groups, resample onto a uniform time
        # grid where gaps are 0 and samples appear as spikes, so sparse data stays
        # visible. _zero_fill_enabled gates the checkbox; _zero_fill_active is the
        # live checkbox value (default on).
        self._zero_fill_enabled: bool = zero_fill
        self._zero_fill_active: bool = True
        self._zero_fill_model: Optional[omni.ui.SimpleBoolModel] = None
        # Ignore intermittent peaks when computing the moving average (default on).
        # Persisted across rebuilds; the checkbox model is recreated each build.
        self._ignore_peaks: bool = True
        self._ignore_peaks_model: Optional[omni.ui.SimpleBoolModel] = None

    def update_monitors(self, monitors: List[PerformanceMonitor]) -> None:
        if not isinstance(monitors, list) and all(isinstance(m, PerformanceMonitor) for m in monitors):
            raise TypeError("monitors must be a List[PerformanceMonitor]")
        self._monitors.clear()
        self._monitors.extend(monitors)

        # Rebuild the series list from scratch so this can be called repeatedly
        # (e.g. as behaviors are added/removed) without duplicating series. The
        # caller is responsible for rebuilding the window UI afterwards so the
        # new series get widgets.
        self._series = [
            _Series(monitor, _PALETTE[idx % len(_PALETTE)])
            for idx, monitor in enumerate(monitors)
        ]

    def _on_collapsed_changed(self, collapsed: bool) -> None:
        # Remember the user's expand/collapse state so rebuilds preserve it.
        self._collapsed = collapsed
        # update() skips collapsed groups, so refresh immediately on expand.
        if not collapsed:
            self.update()

    def construct(self, collapsed: Optional[bool] = None) -> None:
        # Build eagerly: a deferred build_fn reports zero content height inside a
        # content-sized (height=0) parent, so the frame would collapse to nothing.
        if collapsed is not None:
            if not isinstance(collapsed, bool):
                raise TypeError("collapsed must be a bool")
            self._collapsed = collapsed

        # construct() may run again on a window rebuild; drop stale widget refs.
        self._tick_labels = []
        self._avg_tick_labels = []
        self._max_legend_rows = []
        self._avg_legend_rows = []
        for series in self._series:
            series.plot = None
            series.avg_plot = None

        frame = omni.ui.CollapsableFrame(title=self._title, collapsed=self._collapsed)
        frame.set_collapsed_changed_fn(self._on_collapsed_changed)
        with frame:
            # height=0 = size to content, so collapsing an inner frame reclaims
            # its space instead of leaving a fill-VStack blank gap behind.
            with omni.ui.VStack(spacing=8, height=0):
                # Time-window slider: length of x-axis history shown, in seconds.
                with omni.ui.HStack(height=0, spacing=8):
                    omni.ui.Label(
                        "Time window (s):",
                        width=220,
                        alignment=omni.ui.Alignment.LEFT_CENTER,
                    )
                    self._time_window_model = omni.ui.SimpleIntModel(int(self._time_window))
                    self._time_window_model.add_value_changed_fn(self._on_time_window_changed)
                    omni.ui.IntSlider(
                        self._time_window_model,
                        min=self._time_window_min,
                        max=self._time_window_max,
                        width=300,
                    )
                    omni.ui.Spacer()

                # Moving-average window slider: length of the trailing average.
                with omni.ui.HStack(height=0, spacing=8):
                    omni.ui.Label(
                        "Moving avg window (s):",
                        width=220,
                        alignment=omni.ui.Alignment.LEFT_CENTER,
                    )
                    self._average_window_model = omni.ui.SimpleIntModel(int(self._average_window))
                    self._average_window_model.add_value_changed_fn(self._on_average_window_changed)
                    omni.ui.IntSlider(
                        self._average_window_model,
                        min=self._average_window_min,
                        max=self._average_window_max,
                        width=300,
                    )
                    omni.ui.Spacer()

                # Top-N filter slider: the max plot keeps the N highest-max series
                # and the average plot keeps the N highest-average series.
                with omni.ui.HStack(height=0, spacing=8):
                    omni.ui.Label(
                        "Show top N (0 = all):",
                        width=220,
                        alignment=omni.ui.Alignment.LEFT_CENTER,
                    )
                    self._filter_model = omni.ui.SimpleIntModel(self._filter_count)
                    self._filter_model.add_value_changed_fn(self._on_filter_changed)
                    omni.ui.IntSlider(
                        self._filter_model, min=0, max=len(self._series), width=300
                    )
                    omni.ui.Spacer()

                # Zero-fill toggle (only for sporadically-sampled groups): when on,
                # plots are resampled onto a uniform time grid where gaps are 0 and
                # samples appear as spikes, so sparse data stays visible.
                if self._zero_fill_enabled:
                    with omni.ui.HStack(height=0, spacing=8):
                        omni.ui.Label(
                            "Zero-fill gaps:",
                            width=220,
                            alignment=omni.ui.Alignment.LEFT_CENTER,
                        )
                        self._zero_fill_model = omni.ui.SimpleBoolModel(self._zero_fill_active)
                        self._zero_fill_model.add_value_changed_fn(self._on_zero_fill_changed)
                        omni.ui.CheckBox(self._zero_fill_model)
                        omni.ui.Spacer()

                # Top plot: raw samples (expanded by default), with a legend below
                # it filtered/sorted by max.
                raw_frame = omni.ui.CollapsableFrame(title="Max", collapsed=self._raw_collapsed)
                raw_frame.set_collapsed_changed_fn(self._on_raw_collapsed_changed)
                with raw_frame:
                    with omni.ui.VStack(height=0, spacing=8):
                        self._build_plot_region("Latency [ms]", self._tick_labels, average=False)
                        self._build_legend("Max (ms)", self._max_legend_rows)

                # Bottom plot: trailing moving average (collapsed by default), with
                # a legend below it filtered/sorted by moving average. The window
                # length lives on the slider, so labels here stay generic.
                avg_frame = omni.ui.CollapsableFrame(
                    title="Moving Average", collapsed=self._avg_collapsed
                )
                avg_frame.set_collapsed_changed_fn(self._on_avg_collapsed_changed)
                with avg_frame:
                    with omni.ui.VStack(height=0, spacing=8):
                        # Ignore-peaks toggle for the moving average.
                        peak_tooltip = (
                            "Much of the data has a single large peak that should "
                            "not affect the moving average: the moving average is "
                            "meant to convey steady-state performance rather than "
                            "intermittent peaks. When enabled, samples greater than "
                            "5x the largest of the remaining samples are excluded "
                            "from the average."
                        )
                        with omni.ui.HStack(height=0, spacing=8):
                            omni.ui.Label(
                                "Ignore Max Peaks:",
                                width=220,
                                alignment=omni.ui.Alignment.LEFT_CENTER,
                                tooltip=peak_tooltip,
                            )
                            self._ignore_peaks_model = omni.ui.SimpleBoolModel(self._ignore_peaks)
                            self._ignore_peaks_model.add_value_changed_fn(self._on_ignore_peaks_changed)
                            omni.ui.CheckBox(self._ignore_peaks_model, tooltip=peak_tooltip)
                            omni.ui.Spacer()

                        self._build_plot_region("Avg [ms]", self._avg_tick_labels, average=True)
                        self._build_legend("Avg (ms)", self._avg_legend_rows)

        # Populate the freshly built widgets with whatever data we already have.
        self.update()

    def _build_legend(self, value_header: str, rows: list) -> None:
        """Build a legend table (swatch, name, value). Rows are fixed widgets;
        update() fills them in sorted/filtered order."""
        with omni.ui.VStack(height=0, spacing=4):
            # Header row.
            with omni.ui.HStack(width=0, height=0, spacing=12):
                omni.ui.Spacer(width=14)
                omni.ui.Label("Series", width=_NAME_COL_WIDTH,
                              alignment=omni.ui.Alignment.LEFT_CENTER,
                              style={"color": 0xFFAAAAAA})
                omni.ui.Label(value_header, width=120,
                              alignment=omni.ui.Alignment.RIGHT_CENTER,
                              style={"color": 0xFFAAAAAA})

            # One row per series.
            for _ in self._series:
                row = omni.ui.HStack(width=0, height=0, spacing=12)
                with row:
                    swatch = omni.ui.Rectangle(
                        width=14, height=14,
                        style={"background_color": 0x00000000},
                    )
                    name = omni.ui.Label("", width=_NAME_COL_WIDTH,
                                         alignment=omni.ui.Alignment.LEFT_CENTER)
                    value = omni.ui.Label("", width=120,
                                          alignment=omni.ui.Alignment.RIGHT_CENTER)
                    rows.append((row, swatch, name, value))
            omni.ui.Spacer()

    def _build_plot_region(
        self, axis_title: str, tick_labels: List[omni.ui.Label], average: bool
    ) -> None:
        """Build one Y-axis + overlaid line plots region (raw or moving average)."""
        with omni.ui.HStack(height=self._plot_height):
            # Vertical axis title.
            omni.ui.Label(
                "\n".join(axis_title),
                width=14,
                alignment=omni.ui.Alignment.CENTER,
            )

            # Tick values, evenly spaced from the top (scale max) to the bottom (0).
            with omni.ui.VStack(width=70):
                for i in range(self._tick_count):
                    with omni.ui.HStack(height=0):
                        label = omni.ui.Label(
                            "", width=58, alignment=omni.ui.Alignment.RIGHT_CENTER
                        )
                        tick_labels.append(label)
                        # Short tick mark butting up against the plot edge.
                        omni.ui.Rectangle(
                            width=6, height=1, style={"background_color": 0xFF888888}
                        )
                    if i < self._tick_count - 1:
                        omni.ui.Spacer()

            # Overlay one transparent Plot per series on a shared dark background.
            # The background is its own rectangle (not the first plot) so the
            # top-N filter can hide any series' line without removing it.
            with omni.ui.ZStack():
                omni.ui.Rectangle(
                    height=self._plot_height, style={"background_color": 0xFF1A1A1A}
                )
                for series in self._series:
                    plot = omni.ui.Plot(
                        omni.ui.Type.LINE,
                        0.0,
                        1.0,
                        height=self._plot_height,
                        style={"color": series.color, "background_color": 0x00000000},
                    )
                    if average:
                        series.avg_plot = plot
                    else:
                        series.plot = plot

    def update(self):
        # Skip all work when the group is collapsed — nothing is visible, and for
        # a window with many series this is the bulk of the per-tick cost.
        if not self._series or self._collapsed:
            return

        # Anchor the window to the current process clock (the same clock the
        # monitors stamp samples with), NOT the last sample's time. Otherwise a
        # sporadically-sampled group (e.g. misc) freezes once sampling stops,
        # because its max sample time stops advancing.
        now = time.process_time()
        cutoff_time = now - self._time_window
        for series in self._series:
            series.pull_new_data()
            series.prune_old_data(cutoff_time)

        raw_expanded = not self._raw_collapsed
        avg_expanded = not self._avg_collapsed

        # The moving average is only needed for the (expanded) average plot.
        if avg_expanded:
            for series in self._series:
                series.compute_moving_average(self._average_window, self._ignore_peaks)

        # Top-N filter, applied per plot using that plot's ranking metric.
        def top_n(key):
            ordered = sorted(self._series, key=key, reverse=True)
            return ordered if self._filter_count <= 0 else ordered[:self._filter_count]

        # Resample onto a uniform grid spanning the full configured time window so
        # sparse samples are time-positioned (and zero-filled gaps stay visible).
        zero_fill = self._zero_fill_enabled and self._zero_fill_active
        grid_t0 = now - self._time_window
        grid_t1 = now

        if raw_expanded:
            max_visible = top_n(lambda s: s.get_peak())
            max_visible_ids = {id(s) for s in max_visible}
            raw_peak = max((s.get_peak() for s in max_visible), default=0.0)
            raw_scale = float(max(math.ceil(raw_peak * 1.01), 1.0))
            for series in self._series:
                vis = id(series) in max_visible_ids
                series.set_raw_visible(vis)
                if vis:
                    # Only zero-fill sparse series; series with enough points draw
                    # fine as a normal line.
                    raw_vals = (_resample_zero_gaps(series.samples, grid_t0, grid_t1, self._zero_fill_resolution)
                                if zero_fill and len(series.samples) < self._zero_fill_threshold else None)
                    series.plot_raw_data(raw_scale, raw_vals)
            self._update_tick_labels(self._tick_labels, raw_scale)
            self._fill_legend(self._max_legend_rows, max_visible, lambda s: s.get_peak())

        if avg_expanded:
            avg_visible = top_n(lambda s: s.get_avg_peak())
            avg_visible_ids = {id(s) for s in avg_visible}
            avg_peak = max((s.get_avg_peak() for s in avg_visible), default=0.0)
            avg_scale = float(max(math.ceil(avg_peak * 1.01), 1.0))
            for series in self._series:
                vis = id(series) in avg_visible_ids
                series.set_avg_visible(vis)
                if vis:
                    avg_vals = (_resample_zero_gaps(series.avg_samples, grid_t0, grid_t1, self._zero_fill_resolution)
                                if zero_fill and len(series.avg_samples) < self._zero_fill_threshold else None)
                    series.plot_avg_data(avg_scale, avg_vals)
            self._update_tick_labels(self._avg_tick_labels, avg_scale)
            self._fill_legend(self._avg_legend_rows, avg_visible, lambda s: s.get_avg_peak())

    @staticmethod
    def _fill_legend(rows: list, visible: list, value_fn) -> None:
        # One row per visible series (already sorted); hide any leftover rows.
        for row_idx, (row, swatch, name, value) in enumerate(rows):
            if row_idx >= len(visible):
                row.visible = False
                continue
            row.visible = True
            series = visible[row_idx]
            swatch.set_style({"background_color": series.color})
            name.text = series.monitor._name
            value.text = f"{value_fn(series):,.2f}" if series.samples else "--"

    def _on_filter_changed(self, model: "omni.ui.AbstractValueModel") -> None:
        # Persist the slider value (clamped at 0) so it survives window rebuilds,
        # and refresh immediately rather than waiting for the next tick.
        self._filter_count = max(0, model.get_value_as_int())
        self.update()

    def _on_zero_fill_changed(self, model: "omni.ui.AbstractValueModel") -> None:
        # Persist the checkbox state across rebuilds, refresh now.
        self._zero_fill_active = model.get_value_as_bool()
        self.update()

    def _on_ignore_peaks_changed(self, model: "omni.ui.AbstractValueModel") -> None:
        # Persist the checkbox state across rebuilds, refresh now.
        self._ignore_peaks = model.get_value_as_bool()
        self.update()

    def _on_time_window_changed(self, model: "omni.ui.AbstractValueModel") -> None:
        # Clamp to the slider's minimum, persist across rebuilds, refresh now.
        self._time_window = float(max(self._time_window_min, model.get_value_as_int()))
        self.update()

    def _on_average_window_changed(self, model: "omni.ui.AbstractValueModel") -> None:
        # Clamp to the slider's minimum, persist across rebuilds, refresh now.
        self._average_window = float(max(self._average_window_min, model.get_value_as_int()))
        self.update()

    def _on_raw_collapsed_changed(self, collapsed: bool) -> None:
        self._raw_collapsed = collapsed
        # update() skips the collapsed region, so refresh immediately on expand.
        if not collapsed:
            self.update()

    def _on_avg_collapsed_changed(self, collapsed: bool) -> None:
        self._avg_collapsed = collapsed
        if not collapsed:
            self.update()

    def _update_tick_labels(self, tick_labels: List[omni.ui.Label], scale_max: float) -> None:
        # Top label = scale_max, bottom label = 0.
        for i, label in enumerate(tick_labels):
            frac = 1.0 - i / (self._tick_count - 1)
            label.text = f"{scale_max * frac:,.1f}"

    def clear_data(self) -> None:
        # Discard buffered monitor samples and accumulated history so the plot
        # reads empty. Draining the monitors prevents a subsequent rebuild from
        # repopulating the plot from leftover buffered samples.
        for series in self._series:
            series.monitor.pop_durations()
            series.samples = []
            series.avg_samples = []
        self.update()

    def deconstruct(self) -> None:
        self._series = []
        self._tick_labels = []
        self._avg_tick_labels = []
        self._max_legend_rows = []
        self._avg_legend_rows = []


class ProfilerWindow(ExtensionWindow):

    def __init__(self):
        super().__init__("Profiler", width=1800, height=900)
        self._sub = SubscriptionService().create_subscription_set(ProfilerWindow.__name__)
        self._sub.on_timeline_play = self._on_play
        self._sub.on_timeline_stop = self._on_stop
        self._sub.on_timeline_current_time_ticked_permanent = self._on_update_permanent

        self._performance_plots: List[PerformancePlotGroup] = []
        # The permanent-tick plot keeps its data on stop (it ticks regardless of
        # play state); every other plot is cleared on stop.
        self._perm_plot: Optional[PerformancePlotGroup] = None
        # Identities of the dynamically-created monitors (behaviors, physics,
        # custom) currently plotted; used to detect when a set changes (e.g.
        # monitors created on play) and rebuild the window.
        self._dynamic_monitor_ids: Tuple[int, ...] = ()

    def construct(self) -> None:
        sub_handlers = SubscriptionService().subscription_handlers
        tick_monitors = [sh.get_ticked_monitor() for sh in sub_handlers]
        perm_monitors = [sh.get_ticked_permanent_monitor() for sh in sub_handlers]
        behv_monitors = self._gather_behavior_monitors()
        phys_monitors = self._gather_physics_monitors()
        cust_monitors = self._gather_custom_monitors()
        # Snapshot the dynamic monitor set so _sync_dynamic_monitors can detect
        # later changes. Order must match _gather_dynamic_monitors().
        self._dynamic_monitor_ids = tuple(
            id(m) for m in (*behv_monitors, *phys_monitors, *cust_monitors)
        )

        # construct() runs again on every window rebuild, so start from a clean
        # list of plots each time.
        self._performance_plots = []

        with omni.ui.VStack(spacing=8, height=0):
            perm_plot = PerformancePlotGroup("Services (Permanent Tick)")
            perm_plot.update_monitors(perm_monitors)
            perm_plot.construct()
            self._perm_plot = perm_plot
            self._performance_plots.append(perm_plot)

            tick_plot = PerformancePlotGroup("Services (Update)")
            tick_plot.update_monitors(tick_monitors)
            tick_plot.construct()
            self._performance_plots.append(tick_plot)

            # Behaviors, physics and custom monitors are created on play, possibly
            # after this window is constructed. The plots may start empty;
            # _sync_dynamic_monitors rebuilds the window once a monitor set changes.
            behv_plot = PerformancePlotGroup("Behavior Update")
            behv_plot.update_monitors(behv_monitors)
            behv_plot.construct()
            self._performance_plots.append(behv_plot)

            phys_plot = PerformancePlotGroup("Behavior Physics")
            phys_plot.update_monitors(phys_monitors)
            phys_plot.construct()
            self._performance_plots.append(phys_plot)

            misc_plot = PerformancePlotGroup("Miscallaneous", zero_fill=True)
            misc_plot.update_monitors(cust_monitors)
            misc_plot.construct()
            self._performance_plots.append(misc_plot)

        self._sub.enable()

    def _gather_behavior_monitors(self) -> List[PerformanceMonitor]:
        behv_monitors: List[PerformanceMonitor] = []
        for behavior in EntityService().behavior_cache.get_all():
            for runner in behavior.runners:
                behv_monitors.append(runner.update_performance_monitor)
        return behv_monitors

    def _gather_physics_monitors(self) -> List[PerformanceMonitor]:
        phys_monitors: List[PerformanceMonitor] = []
        for behavior in EntityService().behavior_cache.get_all():
            for runner in behavior.runners:
                phys_monitors.append(runner.physics_performance_monitor)
        return phys_monitors
        #return [sh.get_physics_monitor() for sh in SubscriptionService().subscription_handlers]

    def _gather_custom_monitors(self) -> List[PerformanceMonitor]:
        return SubscriptionService().performance_monitors

    def _gather_dynamic_monitors(self) -> List[PerformanceMonitor]:
        # Monitor sets that can change at runtime (e.g. created on play). Order
        # must match the snapshot taken in construct().
        return (
            self._gather_behavior_monitors()
            + self._gather_physics_monitors()
            + self._gather_custom_monitors()
        )

    def _sync_dynamic_monitors(self) -> None:
        # Rebuild the window when any dynamic monitor set changes (behaviors,
        # physics, custom) — created on play, added/removed at runtime, cleared
        # on stop. Comparing object identities avoids rebuilding every tick;
        # construct() re-gathers everything.
        ids = tuple(id(m) for m in self._gather_dynamic_monitors())
        if ids != self._dynamic_monitor_ids:
            self._dynamic_monitor_ids = ids
            self._window.frame.rebuild()

    def _on_play(self) -> None:
        self._sync_dynamic_monitors()

    def _on_stop(self) -> None:
        # Clear every plot's data on stop except the permanent-tick plot, which
        # keeps ticking regardless of play state. Do this before syncing (which
        # may rebuild) so drained monitors don't repopulate the rebuilt plots.
        for plot in self._performance_plots:
            if plot is not self._perm_plot:
                plot.clear_data()
        self._sync_dynamic_monitors()

    def _on_update_permanent(self, current_time: float, delta_time: float) -> None:
        self._sync_dynamic_monitors()
        for performance_plot in self._performance_plots:
            performance_plot.update()


    def deconstruct(self) -> None:
        self._sub.disable()
        for performance_plot in self._performance_plots:
            performance_plot.deconstruct()
