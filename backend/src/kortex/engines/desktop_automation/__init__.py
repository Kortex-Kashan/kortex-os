"""KORTEX Desktop Automation Engine (Phase 6).

Registers `kortex.desktop.launch`/`.click`/`.type`/`.read_text` — the
capability-level surface for native Windows UI automation, executed by the
Desktop Agent (`apps/desktop-agent`) via FlaUI/UI Automation and reached
exclusively through `Kernel.invoke_capability` -> `CapabilityDispatcher` ->
`SecurityEngine` -> the Phase 6 desktop-command transport
(`kortex.engines.agent_gateway`) -> mTLS -> Desktop Agent.
"""
