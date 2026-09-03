"""Exception hierarchy for the KORTEX Operations business module.

All exceptions inherit from `kortex.core.exceptions.KortexError`, participating
in the established platform exception contract.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class OperationsError(KortexError):
    """Base exception for all Operations module errors."""


# -- Vehicle Exceptions -------------------------------------------------------


class OpsVehicleNotFoundError(OperationsError):
    """Raised when a vehicle is not found or belongs to another tenant."""


class OpsVehicleValidationError(OperationsError):
    """Raised when vehicle creation or update parameters fail domain validation."""


class OpsVehicleConflictError(OperationsError):
    """Raised when a vehicle with the same unique identifier (e.g. license plate) already exists in the tenant."""


# -- Tracking Exceptions ------------------------------------------------------


class OpsTrackingRecordValidationError(OperationsError):
    """Raised when an odometer or tracking record violates validation (e.g. non-monotonic reading)."""


# -- Incident Exceptions ------------------------------------------------------


class OpsIncidentNotFoundError(OperationsError):
    """Raised when an incident report is not found or belongs to another tenant."""


class OpsIncidentValidationError(OperationsError):
    """Raised when incident filing or mutation parameters fail domain validation."""


class OpsIncidentConflictError(OperationsError):
    """Raised when an incident with the same incident number already exists in the tenant."""


class OpsIncidentAlreadyClosedError(OperationsError):
    """Raised when attempting to modify, resolve, or re-close a terminal CLOSED incident."""


__all__ = [
    "OperationsError",
    "OpsIncidentAlreadyClosedError",
    "OpsIncidentConflictError",
    "OpsIncidentNotFoundError",
    "OpsIncidentValidationError",
    "OpsTrackingRecordValidationError",
    "OpsVehicleConflictError",
    "OpsVehicleNotFoundError",
    "OpsVehicleValidationError",
]
