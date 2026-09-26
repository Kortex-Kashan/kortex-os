"""KORTEX Browser Capability Layer (Browser-B5).

Registers the `kortex.browser.*` capabilities that let governed AI
orchestration act on a KORTEX Browser surface (Tauri desktop, WebView2),
without ever handing the AI raw WebView2 access, arbitrary JavaScript, or
an unrestricted native command. See `docs/architecture/browser_b5_architecture_gate.md`
for the full design and `docs/architecture/browser_decision_log.md` (D34+)
for the locked B5 V1 decisions this module implements.

B5 V1 scope (this module): capability contracts (`models.py`), registration
into the existing `RegistryEngine`/`CapabilityDispatcher` (`engine.py`), and
the Capability Execution Grant primitive (`grant.py`) that lets a capability
handler — which runs backend-side and has no channel to a live WebView2
surface — hand back a short-lived, signed authorization artifact for the
desktop process to redeem locally, through the *existing*, unchanged
`BrowserRuntime`/`BrowserPolicyEngine` boundary (Browser-B1..B4). No
capability handler in this module executes a real browser action — see
`engine.py`'s own module doc for exactly why and what's deferred to B5.5+.
"""

from __future__ import annotations
