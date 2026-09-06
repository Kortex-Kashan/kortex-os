"""KORTEX Security Engine — email delivery abstraction (Phase A).

Used exclusively by the password-reset flow today. `IEmailProvider` is the
provider-agnostic seam; `DevLogEmailProvider` is the only implementation
that exists (no real SMTP/API email credentials are configured yet) — a
real provider is a drop-in future addition behind the same protocol.
"""
