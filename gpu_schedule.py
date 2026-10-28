# SPDX-License-Identifier: Apache-2.0
"""Admission heuristics, not GPU quotas or guaranteed spare compute capacity."""
DEFAULT_VRAM = {"auto": 2048, "light": 512, "heavy": 8192}


def validate_request(args):
    workload = args.get("workload", "auto")
    if workload not in DEFAULT_VRAM:
        raise ValueError("workload must be auto, light or heavy")
    vram = args.get("vram_mb", DEFAULT_VRAM[workload])
    if isinstance(vram, bool) or not isinstance(vram, int) or not 1 <= vram <= 1048576:
        raise ValueError("vram_mb must be a positive integer estimate in MiB")
    return workload, vram


class Admission:
    def __init__(self, config):
        self.maximum = int(config.get("max_concurrent", 1))
        self.headroom = int(config.get("memory_headroom_mb", 1024))
        self.util_limit = int(config.get("share_utilization_limit", 75))
        self.gap = float(config.get("launch_gap_seconds", 5))
        self.window = float(config.get("sample_window_seconds", 5))
        self.min_samples = int(config.get("minimum_samples", 3))
        self.startup = float(config.get("startup_reservation_seconds", 10))

    def reason(self, job, running, samples, last_start, now):
        if len(running) >= self.maximum:
            return "concurrent job limit reached"
        if any(j.get("workload") == "heavy" for j in running):
            return "heavy job has exclusive GPU Lab access"
        if running and job["workload"] == "heavy":
            return "heavy job is waiting for other GPU Lab jobs to finish"
        if running and now-last_start < self.gap:
            return "waiting for the previous launch to settle"
        recent = [(stamp, value) for stamp, value in samples if now-stamp <= self.window+1]
        if not recent or now-recent[-1][0] > 3:
            # A telemetry outage must not cause a burst of unchecked launches.
            return "GPU telemetry unavailable; allowing only one job" if running else None
        latest = recent[-1][1]
        reserved = sum(j["vram_mb"] for j in running if now-j["started_at"] < self.startup)
        free = latest["memory_total_mib"]-latest["memory_used_mib"]
        if free < self.headroom+job["vram_mb"]+reserved:
            return "waiting for free VRAM (including headroom and startup reservations)"
        peak = max(sample["utilization_percent"] for _, sample in recent)
        # Also consider workloads outside GPU Lab before launching the first job.
        if peak >= self.util_limit:
            return "recent GPU utilization is above the admission limit"
        if running and len(recent) < self.min_samples:
            return "collecting GPU load samples before sharing"
        return None

    def describe(self):
        return {"max_concurrent": self.maximum, "memory_headroom_mb": self.headroom,
                "share_utilization_limit": self.util_limit, "launch_gap_seconds": self.gap,
                "sample_window_seconds": self.window, "minimum_samples": self.min_samples,
                "startup_reservation_seconds": self.startup,
                "default_vram_mb": DEFAULT_VRAM}
