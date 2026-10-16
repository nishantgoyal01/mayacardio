"""Processor module – shared deterministic analytics for MayaCardio.

Implements the per‑second window calculations required by the assessment:

* missing counts per sensor
* completeness flag
* per‑channel mean, min, max (null when no data)
* magnitude (mean of sqrt(ch1²+ch2²) for Sensor 2)
* heart‑rate (derived from Sensor 1 channel 2 peaks)
* mode ("online" or "offline") – passed in by the caller.

All functions are pure and operate on in‑memory sequences of samples,
so they can be used both on‑device and in the cloud FastAPI workers.
"""

import math
import logging
from typing import List, Dict, Tuple, Optional

# Sample representation used throughout the code base
Sample = Tuple[int, Optional[int], Optional[int], Optional[int]]  # (index, ch1, ch2, ch3)


def _channel_stats(values: List[int]) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """Return (mean, min, max) for a list of integer values.
    Returns ``None`` for each field when the input list is empty.
    """
    if not values:
        return None, None, None
    mean = sum(values) / len(values)
    return mean, min(values), max(values)


def _compute_magnitude(sensor2_samples: List[Tuple[int, int, int]]) -> Optional[float]:
    """Mean magnitude for Sensor 2 samples.
    Each element is (index, ch1, ch2).
    Returns ``None`` when no samples are available.
    """
    if not sensor2_samples:
        return None
    mags = [math.sqrt(ch1 * ch1 + ch2 * ch2) for _, ch1, ch2 in sensor2_samples]
    return sum(mags) / len(mags)


def _detect_peaks(sensor1_ch2: List[Tuple[int, int]]) -> List[int]:
    """Detect peaks in Sensor 1 channel 2 according to the assessment rules.

    Returns a list of sample indices that are peaks.
    """
    if len(sensor1_ch2) < 801:  # need at least 800 preceding + current + next
        return []
    peaks = []
    # Pre‑compute max of preceding 800 samples for each position.
    for i in range(800, len(sensor1_ch2) - 1):
        idx, val = sensor1_ch2[i]
        # Ensure preceding 800 samples are present.
        preceding = [v for _, v in sensor1_ch2[i - 800 : i]]
        if len(preceding) < 800:
            continue
        next_val = sensor1_ch2[i + 1][1]
        max_pre = max(preceding)
        if val > sensor1_ch2[i - 1][1] and val > next_val and val > 0.5 * max_pre:
            # Enforce at least 100‑sample gap from previous peak.
            if not peaks or idx - peaks[-1] >= 100:
                peaks.append(idx)
    return peaks


def _heart_rate_for_window(peaks: List[int], window_start: int, window_end: int) -> Optional[float]:
    """Compute heart‑rate for a window using peaks in the extended interval.

    * ``window_start`` is the first index of the window (inclusive).
    * ``window_end`` is the last index of the window (inclusive).
    * The algorithm looks at peaks from ``window_start - 4*400`` up to ``window_end + 400``
      (i.e. five‑second interval surrounding the window as required).
    """
    # Determine the extended interval.
    ext_start = window_start - 4 * 400
    ext_end = window_end + 400
    # Select peaks within the extended interval.
    relevant = [p for p in peaks if ext_start <= p <= ext_end]
    if len(relevant) < 2:
        return None
    # Compute gaps in seconds (sample index difference / 400).
    gaps = [(relevant[i + 1] - relevant[i]) / 400.0 for i in range(len(relevant) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap == 0:
        return None
    hr = 60.0 / mean_gap
    return round(hr, 1)


class Processor:
    """High‑level façade used by the CLI and by the cloud API.

    It pulls raw samples from :class:`Storage`, assembles them per‑sensor, and
    yields NDJSON lines containing the window results.
    """

    def __init__(self, storage):
        self.storage = storage
        self.logger = logging.getLogger(__name__)

    def _load_sensor_samples(self, session_id: str, sensor: int) -> List[Sample]:
        """Return a list of ``(index, ch1, ch2, ch3)`` for *sensor*.
        Missing channels are ``None``.
        """
        rows = []
        cur = self.storage._conn.execute(
            "SELECT sample_index, ch1, ch2, ch3 FROM samples WHERE session_id = ? AND sensor = ? ORDER BY sample_index",
            (session_id, sensor),
        )
        for idx, c1, c2, c3 in cur:
            rows.append((idx, c1, c2, c3))
        return rows

    def compute_results(self, session_id: str):
        """Yield NDJSON strings – one per window – for the given session.
        """
        # Load both sensors' data.
        s1 = self._load_sensor_samples(session_id, 1)
        s2 = self._load_sensor_samples(session_id, 2)
        # Convert to dict keyed by index for quick lookup.
        s1_dict = {idx: (c1, c2, c3) for idx, c1, c2, c3 in s1}
        s2_dict = {idx: (c1, c2) for idx, c1, c2, _ in s2}
        # Determine total number of windows based on max index.
        max_idx = max(max(s1_dict.keys(), default=0), max(s2_dict.keys(), default=0))
        total_windows = (max_idx // 400) + 1
        # Pre‑compute Sensor 1 channel 2 list for peak detection.
        sensor1_ch2 = [(idx, vals[1]) for idx, vals in sorted(s1_dict.items()) if vals[1] is not None]
        peaks = _detect_peaks(sensor1_ch2)

        for w in range(total_windows):
            start = 400 * w
            end = start + 399
            # Missing counts per sensor.
            missing_1 = sum(1 for i in range(start, end + 1) if i not in s1_dict)
            missing_2 = sum(1 for i in range(start, end + 1) if i not in s2_dict)
            complete = missing_1 == 0 and missing_2 == 0

            # Channel statistics – collect available values per channel.
            ch1_vals = [s1_dict[i][0] for i in range(start, end + 1) if i in s1_dict and s1_dict[i][0] is not None]
            ch2_vals = [s1_dict[i][1] for i in range(start, end + 1) if i in s1_dict and s1_dict[i][1] is not None]
            ch3_vals = [s1_dict[i][2] for i in range(start, end + 1) if i in s1_dict and s1_dict[i][2] is not None]
            mean1, min1, max1 = _channel_stats(ch1_vals)
            mean2, min2, max2 = _channel_stats(ch2_vals)
            mean3, min3, max3 = _channel_stats(ch3_vals)

            # Magnitude for Sensor 2.
            sensor2_samples = [(i, s2_dict[i][0], s2_dict[i][1]) for i in range(start, end + 1) if i in s2_dict and s2_dict[i][0] is not None and s2_dict[i][1] is not None]
            magnitude = _compute_magnitude(sensor2_samples)

            # Heart‑rate.
            hr = None
            if w >= 4:
                hr = _heart_rate_for_window(peaks, start, end)

            result = {
                "window": w,
                "missing_1": missing_1,
                "missing_2": missing_2,
                "complete": complete,
                "sensor1": {"mean": mean1, "min": min1, "max": max1},
                "sensor2": {"mean": mean2, "min": min2, "max": max2},
                "sensor3": {"mean": mean3, "min": min3, "max": max3},
                "magnitude": magnitude,
                "heart_rate": hr,
                "mode": self.storage.current_mode(),
            }
            yield json.dumps(result)

    def export(self, session_id: str, source: str):
        """Yield NDJSON lines for either ``device`` (samples + losses) or ``cloud`` (placeholder).
        """
        if source == "device":
            for rec in self.storage.iter_samples(session_id):
                yield json.dumps(rec)
            for loss in self.storage.iter_losses(session_id):
                yield json.dumps(loss)
        else:
            # Cloud export would need a remote request – omitted for brevity.
            raise NotImplementedError("Cloud export not implemented in this module")
