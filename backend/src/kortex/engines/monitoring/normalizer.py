"""DiagnosticsNormalizer for KORTEX Monitoring Engine.

Implements strict 3-tier normalization of IEngineDiagnostics outputs:
- Tier 1: Canonical numeric metrics (finite numbers only; reject NaN/+Inf/-Inf).
- Tier 2: Metadata (status, version, capabilities, timestamps preserved semantically).
- Tier 3: Arbitrary diagnostic payloads (booleans -> 1.0/0.0; None skipped;
  unwhitelisted types preserved in raw payload; first-occurrence-wins for duplicate keys).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.monitoring.constants import (
    ALLOWED_LABEL_KEYS,
    MAX_LABEL_VALUE_LENGTH,
    MAX_LABELS_PER_SERIES,
    METRIC_NAME_REGEX,
)
from kortex.engines.monitoring.models import MetricType

logger = logging.getLogger("kortex.engines.monitoring.normalizer")

# Canonical Tier 2 metadata field names
METADATA_KEYS: frozenset[str] = frozenset(
    {
        "status",
        "version",
        "capabilities",
        "timestamp",
        "dependencies",
        "state",
        "engine",
        "description",
        "mode",
        "health",
    }
)


class NormalizedMetric(BaseModel):
    """Normalized numeric metric ready for registry insertion."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: MetricType
    value: float
    labels: dict[str, str] = Field(default_factory=dict)


class NormalizationResult(BaseModel):
    """Immutable result of normalizing an engine diagnostics payload."""

    model_config = ConfigDict(frozen=True)

    subsystem: str
    metrics: list[NormalizedMetric] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    rejected_count: int = Field(default=0, ge=0)


class DiagnosticsNormalizer:
    """Normalizes arbitrary IEngineDiagnostics outputs into canonical metrics and metadata."""

    def __init__(self) -> None:
        self.metrics_extracted_total: int = 0
        self.metrics_rejected_total: int = 0
        self.invalid_names_total: int = 0
        self.invalid_values_total: int = 0

    def normalize(
        self,
        subsystem: str,
        raw_data: dict[str, Any],
    ) -> NormalizationResult:
        """Normalize a subsystem's raw diagnostic data into Tier 1/2/3 representations."""
        clean_subsystem = subsystem.lower().replace("-", "_").replace(" ", "_")
        seen_metric_keys: set[str] = set()
        extracted_metrics: list[NormalizedMetric] = []
        metadata: dict[str, Any] = {}
        raw_payload: dict[str, Any] = {}
        rejected_in_run: int = 0

        # Process top-level items
        for key, val in raw_data.items():
            raw_payload[key] = val
            key_lower = key.lower()

            # Tier 2: Metadata classification
            if key_lower in METADATA_KEYS and not isinstance(val, (int, float, bool)):
                metadata[key] = val
                continue

            # Process potential metrics or sub-structures
            extracted, rejected = self._process_entry(
                subsystem=clean_subsystem,
                key_path=[key_lower],
                value=val,
                seen_keys=seen_metric_keys,
                base_labels={"subsystem": clean_subsystem},
            )
            extracted_metrics.extend(extracted)
            rejected_in_run += rejected

        self.metrics_extracted_total += len(extracted_metrics)
        self.metrics_rejected_total += rejected_in_run

        return NormalizationResult(
            subsystem=clean_subsystem,
            metrics=extracted_metrics,
            metadata=metadata,
            raw_payload=raw_payload,
            rejected_count=rejected_in_run,
        )

    def _process_entry(
        self,
        subsystem: str,
        key_path: list[str],
        value: Any,
        seen_keys: set[str],
        base_labels: dict[str, str],
    ) -> tuple[list[NormalizedMetric], int]:
        """Process an arbitrary entry with recursive descent for dicts."""
        if value is None:
            # None values are skipped deterministically
            return [], 0

        # Handle nested dictionary
        if isinstance(value, dict):
            extracted: list[NormalizedMetric] = []
            rejected = 0

            # Extract any permitted label attributes from the dict itself
            labels = dict(base_labels)
            for lk in ALLOWED_LABEL_KEYS:
                if lk in value and isinstance(value[lk], (str, int, float)):
                    val_str = str(value[lk]).strip()
                    if len(val_str) <= MAX_LABEL_VALUE_LENGTH and len(labels) < MAX_LABELS_PER_SERIES:
                        labels[lk] = val_str

            for child_k, child_v in value.items():
                if child_k in ALLOWED_LABEL_KEYS:
                    # Skip label key itself as a metric name
                    continue
                child_extracted, child_rejected = self._process_entry(
                    subsystem=subsystem,
                    key_path=[*key_path, str(child_k).lower()],
                    value=child_v,
                    seen_keys=seen_keys,
                    base_labels=labels,
                )
                extracted.extend(child_extracted)
                rejected += child_rejected

            return extracted, rejected

        # Handle booleans (Tier 3 -> normalized to 1.0 or 0.0)
        # Note: bool is a subclass of int in Python, so check bool before int/float
        if isinstance(value, bool):
            numeric_val = 1.0 if value else 0.0
            return self._create_metric(
                subsystem=subsystem,
                key_path=key_path,
                numeric_val=numeric_val,
                metric_type=MetricType.GAUGE,
                seen_keys=seen_keys,
                labels=base_labels,
            )

        # Handle scalar numeric values (int, float)
        if isinstance(value, (int, float)):
            num_float = float(value)
            # Tier 1 rejection of non-finite values (NaN, +Inf, -Inf)
            if not math.isfinite(num_float):
                self.invalid_values_total += 1
                logger.warning(
                    "Rejected non-finite numeric metric '%s' with value %s",
                    ".".join(key_path),
                    num_float,
                )
                return [], 1

            is_counter = key_path[-1].endswith("_total") or key_path[-1].endswith("_count")
            metric_type = MetricType.COUNTER if is_counter else MetricType.GAUGE

            return self._create_metric(
                subsystem=subsystem,
                key_path=key_path,
                numeric_val=num_float,
                metric_type=metric_type,
                seen_keys=seen_keys,
                labels=base_labels,
            )

        # Strings, lists, and arbitrary objects: kept in diagnostic payload, never coerced
        return [], 0

    def _create_metric(
        self,
        subsystem: str,
        key_path: list[str],
        numeric_val: float,
        metric_type: MetricType,
        seen_keys: set[str],
        labels: dict[str, str],
    ) -> tuple[list[NormalizedMetric], int]:
        """Validate naming grammar, enforce first-occurrence-wins, and create NormalizedMetric."""
        # Construct dotted canonical name
        split_segments: list[str] = []
        for seg in key_path:
            split_segments.extend(seg.split("."))

        cleaned_segments: list[str] = []
        for seg in split_segments:
            clean_seg = "".join(c if c.isalnum() or c == "_" else "_" for c in seg)
            cleaned = clean_seg.strip("_")
            if cleaned:
                cleaned_segments.append(cleaned)

        name = ".".join(cleaned_segments)

        # Ensure source-prefixed with subsystem if not already
        if not name.startswith(f"{subsystem}."):
            name = f"{subsystem}.{name}"

        # Grammar validation against METRIC_NAME_REGEX
        if not METRIC_NAME_REGEX.match(name):
            self.invalid_names_total += 1
            logger.warning("Rejected metric name violating grammar: '%s'", name)
            return [], 1

        # First-occurrence-wins rule for duplicate keys
        metric_identity = f"{name}:{sorted(labels.items())}"
        if metric_identity in seen_keys:
            logger.debug("Duplicate metric '%s' encountered; first occurrence wins", name)
            return [], 0

        seen_keys.add(metric_identity)
        metric = NormalizedMetric(
            name=name,
            type=metric_type,
            value=numeric_val,
            labels=labels,
        )
        return [metric], 0
