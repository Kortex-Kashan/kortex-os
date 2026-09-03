"""Exception hierarchy for the KORTEX HR & Payroll business module.

All exceptions inherit from `kortex.core.exceptions.KortexError`, participating
in the established platform exception contract.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class HRPayrollError(KortexError):
    """Base exception for all HR & Payroll module errors."""


# -- Employee Exceptions ------------------------------------------------------


class HREmployeeNotFoundError(HRPayrollError):
    """Raised when an employee is not found or belongs to another tenant."""


class HREmployeeValidationError(HRPayrollError):
    """Raised when employee creation/update parameters fail validation."""


class HREmployeeConflictError(HRPayrollError):
    """Raised when an employee with the same code already exists within the tenant."""


# -- Attendance Exceptions ----------------------------------------------------


class HRAttendanceNotFoundError(HRPayrollError):
    """Raised when an attendance record is not found or belongs to another tenant."""


class HRAttendanceValidationError(HRPayrollError):
    """Raised when attendance check-in/check-out parameters are invalid."""


class HRAttendanceConflictError(HRPayrollError):
    """Raised when an attendance record already exists for the employee on the date."""


# -- Leave Exceptions ---------------------------------------------------------


class HRLeaveNotFoundError(HRPayrollError):
    """Raised when a leave request is not found or belongs to another tenant."""


class HRLeaveValidationError(HRPayrollError):
    """Raised when leave request parameters are invalid."""


class HRLeaveBalanceExceededError(HRPayrollError):
    """Raised when requested leave exceeds the employee's available balance."""


class HRLeaveOverlapError(HRPayrollError):
    """Raised when a leave request overlaps with an existing pending/approved request."""


# -- Payroll Exceptions -------------------------------------------------------


class HRPayrollRunNotFoundError(HRPayrollError):
    """Raised when a payroll run is not found or belongs to another tenant."""


class HRPayrollRunValidationError(HRPayrollError):
    """Raised when payroll run calculation parameters are invalid."""


class HRPayrollRunAlreadyFinalizedError(HRPayrollError):
    """Raised when attempting to recalculate, modify, or delete a FINALIZED payroll run."""


__all__ = [
    "HRAttendanceConflictError",
    "HRAttendanceNotFoundError",
    "HRAttendanceValidationError",
    "HREmployeeConflictError",
    "HREmployeeNotFoundError",
    "HREmployeeValidationError",
    "HRLeaveBalanceExceededError",
    "HRLeaveNotFoundError",
    "HRLeaveOverlapError",
    "HRLeaveValidationError",
    "HRPayrollError",
    "HRPayrollRunAlreadyFinalizedError",
    "HRPayrollRunNotFoundError",
    "HRPayrollRunValidationError",
]
