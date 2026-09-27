//! Browser-B5 (click/type/read/extract execution): the UIA execution
//! foundation, built directly from the empirical findings of three
//! dedicated architecture gates (`docs/architecture/browser_b5_master_plan.md`
//! §5.2/§5.4/§5.5 — D44/D45/D46) rather than from first principles.
//!
//! **Every UIA call in this module runs on a dedicated worker thread from
//! [`UiaWorkerPool`] — never inside a `with_webview`/`with_core_webview2`
//! closure.** Issuing a synchronous UIA call from WebView2's own STA thread
//! risks a real, documented deadlock (Microsoft Learn, "Understanding
//! Threading Issues": UI Automation clients should use a dedicated MTA
//! thread that "should not own any windows"). This module's own worker
//! threads are exactly that dedicated MTA thread.
//!
//! **Worker creation is serialized by construction, not by convention.**
//! The live spike for this gate proved, twice, that two threads calling
//! `CoCreateInstance(CUIAutomation, ...)` at nearly the same instant is
//! unsafe — one call consistently failed with a generic `E_FAIL`. Every new
//! worker is therefore created while [`UiaWorkerPool`] holds its own
//! `std::sync::Mutex` — the SAME lock `submit` itself uses to find an idle
//! worker — so two workers can never be created concurrently; the mutex
//! itself is the serialization guarantee, not a separate "warm up at
//! startup" phase.
//!
//! **A hung UIA call is never force-terminated.** `TerminateThread` is
//! Microsoft's own documented "dangerous function... should only be used in
//! the most extreme cases" (can leave a critical section unreleased, a heap
//! lock held, or shared DLL state corrupted) — this module never calls it.
//! `tokio::time::timeout` on the CALLING side only abandons the `.await`; it
//! does not and cannot cancel the underlying blocked native call (this is
//! stated explicitly, not assumed). A timed-out worker is [`retire`]d —
//! removed from the pool, never reused — and a replacement is created (up to
//! a hard, process-lifetime cumulative cap) to restore capacity. The
//! abandoned worker's own OS thread is deliberately leaked (accepted,
//! bounded tradeoff — see the module's own cap) rather than force-killed.
//!
//! **A UIA element (or any raw COM/UIA object) never crosses out of a
//! worker thread.** Every [`UiaOperationKind`] carries only plain data in;
//! every [`UiaOutcome`] carries only plain data out (`String`/`bool`/a small
//! typed struct) — this is what makes it safe for [`UiaJob`] to cross to a
//! worker via a plain `std::sync::mpsc` channel and for the result to cross
//! back via a `tokio::sync::oneshot` channel.
//!
//! **A resolved element is never retained across calls.** The live spike
//! for this gate proved that a held `IUIAutomationElement` does NOT
//! reliably raise `UIA_E_ELEMENTNOTAVAILABLE` after its surface navigates
//! away — it can silently begin reporting the NEW page's own content
//! instead. UIA's own staleness signal is therefore never trusted as a
//! security mechanism anywhere in this module; the caller's own
//! Grant-verified `navigation_generation` check (`browser_grant.rs`,
//! re-verified immediately before every `UiaWorkerPool::submit` call) is the
//! only safeguard, and every element this module resolves is used
//! immediately and discarded within the same worker-thread call, never
//! returned or cached.
//!
//! **`node_ref`-only resolution is not supported** (`UiaSelectorSpec` has no
//! `node_ref` field at all — `role`/`accessible_name` only). See the master
//! plan §5.5 for why: a `node_ref` that ever meant a cached UIA object would
//! contradict the finding above; a `node_ref` that means a re-resolved
//! selector recipe is a real, separate design this module does not build.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;

#[cfg(windows)]
use windows::Win32::Foundation::HWND;
#[cfg(windows)]
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED,
};
#[cfg(windows)]
use windows::Win32::UI::Accessibility::{
    CUIAutomation, IUIAutomation, IUIAutomationElement, IUIAutomationInvokePattern,
    IUIAutomationValuePattern, TreeScope_Descendants, UIA_InvokePatternId, UIA_ValuePatternId,
    UIA_CONTROLTYPE_ID,
};

/// A resolved target for `.click`/`.type`, or one field of a `.extract`
/// request. Deliberately mirrors ONLY the two fields
/// `backend/src/kortex/engines/browser/models.py::BrowserElementSelector`
/// fields this module actually supports — `node_ref` is not represented
/// here at all (see the module's own doc comment on why).
#[derive(Debug, Clone)]
pub struct UiaSelectorSpec {
    pub role: Option<String>,
    pub accessible_name: Option<String>,
}

/// Bounds on how much a single `.read`/`.extract`/traversal may cost —
/// applied inside the worker, never left to the caller's own discretion,
/// so a hostile or merely enormous page cannot make one operation run
/// unboundedly long or return an unboundedly large result.
pub struct UiaBounds {
    pub read_max_chars: usize,
    pub max_extract_fields: usize,
    pub max_ancestor_depth: u32,
    pub max_elements_scanned: i32,
}

impl Default for UiaBounds {
    fn default() -> Self {
        Self {
            read_max_chars: 20_000,
            max_extract_fields: 50,
            // The live spike observed containment proofs completing in 2
            // hops; this is a generous multiple, not a tight fit to that one
            // observation, since real pages can plausibly nest deeper.
            max_ancestor_depth: 25,
            max_elements_scanned: 5_000,
        }
    }
}

/// The one, uniform shape every UIA operation this module supports takes.
/// Plain data only (see the module's own doc comment on why).
#[derive(Debug, Clone)]
pub enum UiaOperationKind {
    Read,
    Extract {
        fields: Vec<(String, UiaSelectorSpec)>,
    },
    Click {
        selector: UiaSelectorSpec,
    },
    Type {
        selector: UiaSelectorSpec,
        text: String,
    },
}

#[derive(Debug, Clone, Serialize)]
pub struct ExtractedField {
    pub accessible_name: String,
    pub control_type: String,
    pub value: Option<String>,
}

/// The one, uniform shape every UIA operation's success case takes. Plain
/// data only.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum UiaOutcome {
    Read {
        text: String,
        truncated: bool,
    },
    Extract {
        fields: HashMap<String, ExtractedField>,
    },
    Click,
    Type,
}

/// Every typed failure this module can report. Never a raw COM `HRESULT`
/// crosses out of a worker thread — every native failure is mapped to one
/// of these before it ever reaches `browser_grant.rs`.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum UiaExecutionError {
    /// `ElementFromHandle` itself failed — the surface's UIA root could not
    /// be obtained at all.
    RootUnavailable,
    /// The accessibility tree had not populated within the operation's own
    /// bounded readiness window (see `resolve_root_with_readiness`) —
    /// distinct from `SelectorNotFound`, which means the tree WAS ready but
    /// the requested element genuinely isn't in it.
    AccessibilityNotReady,
    SelectorNotFound,
    SelectorAmbiguous {
        match_count: i32,
    },
    /// The selector itself cannot be safely represented — an unrecognized
    /// `role` string, or (deliberately) a selector supplying neither `role`
    /// nor `accessible_name` (`node_ref`-only, not supported in V1).
    SelectorUnsupported {
        reason: String,
    },
    /// The resolved element's ancestor chain never reached the
    /// Grant-authorized surface's own HWND within the bounded walk.
    ContainmentFailed,
    UnsupportedControlPattern,
    ComFailure {
        message: String,
    },
    /// The worker pool had no idle worker, was already at its maximum
    /// concurrent-worker count, or had already exhausted its process-
    /// lifetime cumulative worker-creation cap.
    PoolExhausted,
}
// Note: the caller-side timeout (the operation's own `tokio::time::timeout`
// elapsing) is reported as `BrowserGrantExecutionError::Timeout`
// (`browser_grant.rs`), not as a variant here -- this module's own internal
// deadline (`UiaJob::deadline`) is used ONLY to bound the accessibility-
// readiness poll (`resolve_root_with_readiness`), which reports
// `AccessibilityNotReady` on its own expiry, a DIFFERENT, more specific
// outcome than a bare timeout.

#[cfg(windows)]
impl std::fmt::Display for UiaExecutionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
#[cfg(windows)]
impl std::error::Error for UiaExecutionError {}

/// One pending piece of work handed to a worker thread. `deadline` is a
/// concrete `Instant` the WORKER itself respects during its own bounded
/// accessibility-readiness poll (see `resolve_root_with_readiness`) — the
/// caller's own `tokio::time::timeout` is a SEPARATE, independent bound on
/// the same operation; either one firing first is a legitimate `Timeout`.
struct UiaJob {
    surface_hwnd: isize,
    operation: UiaOperationKind,
    deadline: Instant,
    respond_to: tokio::sync::oneshot::Sender<Result<UiaOutcome, UiaExecutionError>>,
}

/// The pieces `spawn_and_warm_up_one_worker` hands back for a freshly
/// created, already-warmed-up worker: its slot id, the channel used to send
/// it jobs, its shared busy flag, and its `JoinHandle` (owned by the
/// resulting `WorkerSlot` so `UiaWorkerPool`'s own `Drop` can join it).
#[cfg(windows)]
type NewWorkerHandles = (
    u64,
    std::sync::mpsc::Sender<UiaJob>,
    Arc<AtomicBool>,
    std::thread::JoinHandle<()>,
);

struct WorkerSlot {
    id: u64,
    sender: std::sync::mpsc::Sender<UiaJob>,
    busy: Arc<AtomicBool>,
    /// Owned so [`UiaWorkerPool`]'s own `Drop` can join it -- see that impl's
    /// doc comment for why joining a HEALTHY worker (never a retired/
    /// abandoned one) at pool-teardown time is both safe and necessary.
    handle: std::thread::JoinHandle<()>,
}

/// A bounded, resource-safe pool of dedicated UIA worker threads.
///
/// Deliberately NOT a queue-based design: `submit` either dispatches to an
/// already-idle worker, creates one more (serially, under this struct's own
/// lock — see the module doc comment) if under `max_workers`, or fails
/// closed immediately with `PoolExhausted`. There is no pending-work queue
/// at all — a queue of unbounded (or even bounded-but-nonzero) depth is
/// itself a resource an attacker could fill; "fail closed now" is simpler
/// and strictly safer than "queue and hope capacity frees up."
pub struct UiaWorkerPool {
    slots: Mutex<Vec<WorkerSlot>>,
    next_slot_id: AtomicU64,
    total_created: AtomicU64,
    max_workers: usize,
    max_lifetime_creations: u64,
}

impl UiaWorkerPool {
    /// `max_workers`: the largest number of UIA operations this process
    /// will ever run concurrently. Sized small and deliberately, not
    /// arbitrarily large — a single interactive AI-driven browsing session
    /// is not expected to need more than a handful of simultaneous
    /// click/type/read/extract calls in flight at once, and every worker is
    /// a real, dedicated OS thread plus a live COM object for the process's
    /// entire remaining lifetime once created.
    /// `max_lifetime_creations`: the hard cap on how many workers this pool
    /// will EVER create across the process's whole lifetime, healthy or
    /// abandoned — this is what bounds total leaked-thread growth even
    /// under a sustained, repeated-timeout attack (each abandoned worker
    /// consumes one unit of this budget permanently; once exhausted, every
    /// further `submit` fails closed with `PoolExhausted` regardless of how
    /// many workers are currently healthy).
    pub fn new(max_workers: usize, max_lifetime_creations: u64) -> Self {
        Self {
            slots: Mutex::new(Vec::new()),
            next_slot_id: AtomicU64::new(0),
            total_created: AtomicU64::new(0),
            max_workers,
            max_lifetime_creations,
        }
    }

    #[cfg(windows)]
    fn spawn_and_warm_up_one_worker(&self) -> Result<NewWorkerHandles, UiaExecutionError> {
        let id = self.next_slot_id.fetch_add(1, Ordering::SeqCst);
        let (job_tx, job_rx) = std::sync::mpsc::channel::<UiaJob>();
        let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
        let busy = Arc::new(AtomicBool::new(false));
        let busy_for_worker = busy.clone();
        let handle =
            std::thread::spawn(move || uia_worker_thread_main(job_rx, ready_tx, busy_for_worker));
        // Bounded wait for the new worker's own COM-initialization
        // handshake -- this is the ONLY place `submit` can block for more
        // than a few microseconds, which is why every call site in
        // `browser_grant.rs` wraps `submit` in `tokio::task::spawn_blocking`
        // rather than calling it inline from an async task.
        match ready_rx.recv_timeout(Duration::from_secs(5)) {
            Ok(Ok(())) => {
                self.total_created.fetch_add(1, Ordering::SeqCst);
                Ok((id, job_tx, busy, handle))
            }
            Ok(Err(message)) => Err(UiaExecutionError::ComFailure { message }),
            Err(_) => Err(UiaExecutionError::ComFailure {
                message: "UIA worker did not report readiness within 5s".to_string(),
            }),
        }
    }

    /// Dispatches one operation. Returns the slot id (needed for `retire`
    /// if the caller's own timeout elapses) and the receiver half of the
    /// result channel. Blocking (see `spawn_and_warm_up_one_worker`'s own
    /// doc comment) — callers must wrap this in `tokio::task::spawn_blocking`.
    #[cfg(windows)]
    pub fn submit(
        &self,
        surface_hwnd: isize,
        operation: UiaOperationKind,
        timeout: Duration,
    ) -> Result<
        (
            u64,
            tokio::sync::oneshot::Receiver<Result<UiaOutcome, UiaExecutionError>>,
        ),
        UiaExecutionError,
    > {
        let (tx, rx) = tokio::sync::oneshot::channel();
        let deadline = Instant::now() + timeout;
        let mut job = UiaJob {
            surface_hwnd,
            operation,
            deadline,
            respond_to: tx,
        };
        let mut slots = self.slots.lock().unwrap();
        let idle_index = slots.iter().position(|s| !s.busy.load(Ordering::SeqCst));
        if let Some(idx) = idle_index {
            slots[idx].busy.store(true, Ordering::SeqCst);
            match slots[idx].sender.send(job) {
                Ok(()) => return Ok((slots[idx].id, rx)),
                Err(std::sync::mpsc::SendError(returned_job)) => {
                    // The idle worker's own OS thread had already died
                    // without this pool noticing (its channel is closed) --
                    // drop that stale slot, recover the still-unsent job
                    // (mpsc hands the value back on a failed send), and
                    // fall through to creating a fresh worker instead.
                    job = returned_job;
                    slots.remove(idx);
                }
            }
        }
        if slots.len() >= self.max_workers {
            return Err(UiaExecutionError::PoolExhausted);
        }
        if self.total_created.load(Ordering::SeqCst) >= self.max_lifetime_creations {
            return Err(UiaExecutionError::PoolExhausted);
        }
        let (id, job_tx, busy, handle) = self.spawn_and_warm_up_one_worker()?;
        busy.store(true, Ordering::SeqCst);
        let _ = job_tx.send(job);
        slots.push(WorkerSlot {
            id,
            sender: job_tx,
            busy,
            handle,
        });
        Ok((id, rx))
    }

    /// Removes a slot from the pool permanently -- called by
    /// `browser_grant.rs` when its own `tokio::time::timeout` elapses
    /// waiting for this slot's result. The underlying OS thread is NOT
    /// killed (see the module's own doc comment on why) -- it is simply
    /// disconnected from the pool's own bookkeeping, so it can never be
    /// dispatched to again even if it eventually finishes. Deliberately
    /// does NOT join the retired slot's `handle` -- this is the one path
    /// where the worker may genuinely be wedged in a hung native call, and
    /// joining it here would risk exactly the hang this whole design exists
    /// to avoid (see [`UiaWorkerPool`]'s own `Drop`, which joins ONLY
    /// still-healthy slots, never a retired one).
    pub fn retire(&self, slot_id: u64) {
        let mut slots = self.slots.lock().unwrap();
        slots.retain(|s| s.id != slot_id);
    }

    /// Test-only sibling of `retire` that additionally joins the removed
    /// slot's worker thread before returning.
    ///
    /// **Why this exists, and why it is safe here but NOT in `retire`
    /// itself**: `retire`'s own contract is "never join -- the worker might
    /// be genuinely wedged in a hung native call" (see its own doc
    /// comment). But a test that retires a worker it JUST used successfully
    /// (i.e. known-healthy, not suspected-hung) is a fundamentally
    /// different situation: dropping the slot's `sender` closes its job
    /// channel, which is guaranteed to make a healthy, idle worker's
    /// `recv()` return and its thread exit almost immediately -- joining is
    /// safe and fast, never a real wait. Without this, a live regression
    /// run reproduced a genuine `STATUS_ACCESS_VIOLATION`: `retire`'s own
    /// detached worker thread called `CoUninitialize()` asynchronously,
    /// unsynchronized, while a LATER test's fresh worker concurrently
    /// called `CoCreateInstance(CUIAutomation)` -- the identical
    /// first-instantiation hazard the architecture gate's live spike
    /// already found (see the module's own doc comment), just triggered by
    /// retirement-teardown timing instead of simultaneous creation. This
    /// is a genuine, disclosed residual risk of production `retire`'s own
    /// "never join" design too (see `browser_known_limitations.md`) --
    /// this test-only method exists so the test suite does not ALSO
    /// exercise that narrow window on every run, not to hide it.
    #[cfg(test)]
    fn retire_and_join_for_test(&self, slot_id: u64) {
        let removed = {
            let mut slots = self.slots.lock().unwrap();
            let idx = slots.iter().position(|s| s.id == slot_id);
            idx.map(|i| slots.remove(i))
        };
        if let Some(slot) = removed {
            drop(slot.sender);
            let _ = slot.handle.join();
        }
    }

    #[cfg(test)]
    fn healthy_worker_count(&self) -> usize {
        self.slots.lock().unwrap().len()
    }

    #[cfg(test)]
    fn total_created_count(&self) -> u64 {
        self.total_created.load(Ordering::SeqCst)
    }

    /// Test-only: deterministically forces a specific, already-created
    /// worker back to "busy" without depending on real job-completion
    /// timing. Exists because a real UIA job against an unresolvable HWND
    /// completes in microseconds (see `resolve_root_with_readiness`'s first
    /// `ElementFromHandle` call failing immediately) -- too fast to reliably
    /// observe "still busy" from a second thread without an artificial,
    /// flaky race. This lets a test prove the `max_workers` cap's own logic
    /// deterministically, on top of a genuinely-created real worker.
    #[cfg(test)]
    #[cfg(windows)]
    fn mark_slot_busy_for_test(&self, slot_id: u64) {
        let slots = self.slots.lock().unwrap();
        if let Some(slot) = slots.iter().find(|s| s.id == slot_id) {
            slot.busy.store(true, Ordering::SeqCst);
        }
    }
}

/// Joins every still-healthy worker's OS thread before the pool itself is
/// considered gone.
///
/// **Why this exists**: dropping a slot's `sender` closes that worker's job
/// channel, which makes its `recv()` loop exit and call `CoUninitialize()`
/// -- but that happens ON THE WORKER'S OWN THREAD, asynchronously. Without
/// an explicit join, `UiaWorkerPool::drop` could return while a worker
/// thread is still mid-teardown (or hasn't even observed the closed channel
/// yet). In production this never matters (`lib.rs` creates exactly one
/// pool, managed by Tauri for the whole process lifetime, never dropped
/// early) -- but it matters a great deal for tests, which each construct
/// their own short-lived pool: a live regression run of this module's own
/// test suite crashed the whole test binary with `STATUS_ACCESS_VIOLATION`
/// under `--test-threads=1` (so NOT a cross-test-thread race) the moment
/// one test's pool was dropped while a fresh worker in the very next test's
/// pool was concurrently calling `CoInitializeEx`/`CoCreateInstance` on a
/// brand-new OS thread -- i.e. a torn-down worker's `CoUninitialize()` and a
/// new worker's `CoCreateInstance(CUIAutomation)` racing on the SAME
/// process, exactly the class of first-instantiation hazard the
/// architecture gate's live spike already found (see the module's own doc
/// comment), just triggered by teardown ordering rather than concurrent
/// creation. Joining here closes that window: by the time `drop` returns,
/// every healthy worker has already called `CoUninitialize()` and exited.
///
/// **Deliberately excludes retired slots** -- those were already removed
/// from `self.slots` by `retire()` precisely because they might be wedged
/// in a hung native call; only slots still present here are ones this pool
/// itself believes are healthy and idle-or-briefly-busy, so joining them is
/// expected to be fast, not a reintroduction of the "never risk a hang"
/// violation `retire()` itself exists to avoid.
impl Drop for UiaWorkerPool {
    fn drop(&mut self) {
        let slots = std::mem::take(&mut *self.slots.lock().unwrap());
        for slot in slots {
            drop(slot.sender);
            let _ = slot.handle.join();
        }
    }
}

#[cfg(windows)]
fn uia_worker_thread_main(
    job_rx: std::sync::mpsc::Receiver<UiaJob>,
    ready_tx: std::sync::mpsc::Sender<Result<(), String>>,
    busy: Arc<AtomicBool>,
) {
    let hr = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
    if hr.is_err() {
        let _ = ready_tx.send(Err(format!("CoInitializeEx failed: {hr:?}")));
        return;
    }
    let automation: IUIAutomation =
        match unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) } {
            Ok(a) => a,
            Err(e) => {
                let _ = ready_tx.send(Err(format!("CoCreateInstance(CUIAutomation) failed: {e}")));
                unsafe { CoUninitialize() };
                return;
            }
        };
    if ready_tx.send(Ok(())).is_err() {
        // The pool's own 5s readiness handshake already timed out and gave
        // up on this worker -- there is no slot referencing it. Exit
        // cleanly rather than sitting idle forever with nothing to do.
        // `automation` MUST be released (see the doc comment below on the
        // matching drop-before-CoUninitialize ordering) before tearing down
        // the apartment it was created in.
        drop(automation);
        unsafe { CoUninitialize() };
        return;
    }

    let bounds = UiaBounds::default();
    while let Ok(job) = job_rx.recv() {
        let result = execute_uia_operation(&automation, &job, &bounds);
        let _ = job.respond_to.send(result);
        busy.store(false, Ordering::SeqCst);
    }
    // `automation` (a COM interface pointer) MUST be released -- via an
    // explicit `drop`, calling its `Release()` -- BEFORE `CoUninitialize()`
    // tears down this thread's COM apartment. Rust's own implicit,
    // end-of-scope drop order would do this backwards (locals drop AFTER
    // the function's last statement runs, so `CoUninitialize()` would
    // execute first, then `Release()` on an interface whose apartment no
    // longer exists) -- undefined behavior that reproduced as a genuine,
    // deterministic `STATUS_ACCESS_VIOLATION` the first time a caller
    // actually waited (via `UiaWorkerPool`'s own `Drop`, which joins this
    // thread) for this function to fully return, rather than letting it
    // finish detached and unobserved.
    drop(automation);
    unsafe { CoUninitialize() };
}

#[cfg(windows)]
fn execute_uia_operation(
    automation: &IUIAutomation,
    job: &UiaJob,
    bounds: &UiaBounds,
) -> Result<UiaOutcome, UiaExecutionError> {
    let root = resolve_root_with_readiness(automation, job.surface_hwnd, job.deadline, bounds)?;
    match &job.operation {
        UiaOperationKind::Read => {
            let (text, truncated) = collect_bounded_text(automation, &root, bounds)?;
            Ok(UiaOutcome::Read { text, truncated })
        }
        UiaOperationKind::Extract { fields } => {
            if fields.len() > bounds.max_extract_fields {
                return Err(UiaExecutionError::SelectorUnsupported {
                    reason: format!(
                        "extract requested {} fields, bounded maximum is {}",
                        fields.len(),
                        bounds.max_extract_fields
                    ),
                });
            }
            let mut out = HashMap::with_capacity(fields.len());
            for (field_name, selector) in fields {
                // Fails the WHOLE extract on the first field that cannot be
                // resolved -- never silently returns a partial result set
                // that looks complete (this project's own explicit
                // requirement).
                let element =
                    resolve_unique_element(automation, &root, job.surface_hwnd, selector, bounds)?;
                let accessible_name = unsafe { element.CurrentName() }
                    .map(|b| b.to_string())
                    .unwrap_or_default();
                let control_type = unsafe { element.CurrentControlType() }.ok();
                let value = unsafe {
                    element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
                }
                .ok()
                .and_then(|vp| unsafe { vp.CurrentValue() }.ok())
                .map(|b| b.to_string());
                out.insert(
                    field_name.clone(),
                    ExtractedField {
                        accessible_name,
                        control_type: control_type_to_string(control_type),
                        value,
                    },
                );
            }
            Ok(UiaOutcome::Extract { fields: out })
        }
        UiaOperationKind::Click { selector } => {
            let element =
                resolve_unique_element(automation, &root, job.surface_hwnd, selector, bounds)?;
            let invoke = unsafe {
                element.GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
            }
            .map_err(|_| UiaExecutionError::UnsupportedControlPattern)?;
            unsafe { invoke.Invoke() }.map_err(|e| UiaExecutionError::ComFailure {
                message: e.to_string(),
            })?;
            Ok(UiaOutcome::Click)
        }
        UiaOperationKind::Type { selector, text } => {
            // Deliberately NOT re-implementing the sensitive-input gate
            // here: `browser.type`'s existing refusal
            // (`grant.py::is_sensitive_type_target`/`looks_like_secret_value`)
            // already runs BEFORE a Grant is ever minted, and this worker is
            // only ever reached via a Grant that already passed it -- there
            // is no second, lower-level path into this function that could
            // bypass it (see `browser_grant.rs::execute_type`, which is the
            // only caller).
            let element =
                resolve_unique_element(automation, &root, job.surface_hwnd, selector, bounds)?;
            let value_pattern = unsafe {
                element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
            }
            .map_err(|_| UiaExecutionError::UnsupportedControlPattern)?;
            let bstr = windows::core::BSTR::from(text.as_str());
            unsafe { value_pattern.SetValue(&bstr) }.map_err(|e| {
                UiaExecutionError::ComFailure {
                    message: e.to_string(),
                }
            })?;
            Ok(UiaOutcome::Type)
        }
    }
}

/// Accessibility-tree-readiness heuristic. REASONED, not guaranteed
/// correct: the live spike observed exactly two data points (a freshly-
/// loaded page reporting 15 shallow elements before real content appeared,
/// and 20 richer elements ~8s later) -- `READINESS_MIN_DESCENDANTS` is
/// picked to sit between them, not derived from a larger sample. Disclosed
/// explicitly as a known limitation, not asserted as precise.
#[cfg(windows)]
const READINESS_MIN_DESCENDANTS: i32 = 16;
#[cfg(windows)]
const READINESS_POLL_INTERVAL: Duration = Duration::from_millis(500);

#[cfg(windows)]
fn resolve_root_with_readiness(
    automation: &IUIAutomation,
    hwnd_value: isize,
    deadline: Instant,
    bounds: &UiaBounds,
) -> Result<IUIAutomationElement, UiaExecutionError> {
    let hwnd = HWND(hwnd_value as *mut core::ffi::c_void);
    loop {
        let root = unsafe { automation.ElementFromHandle(hwnd) }
            .map_err(|_| UiaExecutionError::RootUnavailable)?;
        if let Ok(count) = descendant_count(automation, &root, bounds) {
            if count >= READINESS_MIN_DESCENDANTS {
                return Ok(root);
            }
        }
        if Instant::now() >= deadline {
            return Err(UiaExecutionError::AccessibilityNotReady);
        }
        std::thread::sleep(
            READINESS_POLL_INTERVAL.min(deadline.saturating_duration_since(Instant::now())),
        );
    }
}

#[cfg(windows)]
fn descendant_count(
    automation: &IUIAutomation,
    root: &IUIAutomationElement,
    bounds: &UiaBounds,
) -> windows::core::Result<i32> {
    let true_condition = unsafe { automation.CreateTrueCondition() }?;
    let all = unsafe { root.FindAll(TreeScope_Descendants, &true_condition) }?;
    let count = unsafe { all.Length() }?;
    Ok(count.min(bounds.max_elements_scanned))
}

/// Reads bounded, plain text from every non-empty accessible name in the
/// surface's current tree. Never returns raw COM/UIA objects, cookies,
/// storage, or filesystem data -- only names UIA itself already exposes as
/// plain strings. `browser.read`'s entire information boundary: accessible
/// names, bounded in count and total length.
#[cfg(windows)]
fn collect_bounded_text(
    automation: &IUIAutomation,
    root: &IUIAutomationElement,
    bounds: &UiaBounds,
) -> Result<(String, bool), UiaExecutionError> {
    let true_condition =
        unsafe { automation.CreateTrueCondition() }.map_err(|e| UiaExecutionError::ComFailure {
            message: e.to_string(),
        })?;
    let all = unsafe { root.FindAll(TreeScope_Descendants, &true_condition) }.map_err(|e| {
        UiaExecutionError::ComFailure {
            message: e.to_string(),
        }
    })?;
    let count = unsafe { all.Length() }
        .unwrap_or(0)
        .min(bounds.max_elements_scanned);
    let mut buffer = String::new();
    let mut truncated = false;
    for i in 0..count {
        let Ok(el) = (unsafe { all.GetElement(i) }) else {
            continue;
        };
        let Ok(name) = (unsafe { el.CurrentName() }) else {
            continue;
        };
        let name = name.to_string();
        let trimmed = name.trim();
        if trimmed.is_empty() {
            continue;
        }
        if !buffer.is_empty() {
            buffer.push(' ');
        }
        buffer.push_str(trimmed);
        if buffer.len() >= bounds.read_max_chars {
            buffer.truncate(bounds.read_max_chars);
            truncated = true;
            break;
        }
    }
    Ok((buffer, truncated))
}

/// Maps the typed contract's free-form `role` string onto UIA's own
/// `ControlType` vocabulary. Deliberately a small, explicit, disclosed
/// vocabulary -- not an attempt at full coverage of every UIA control type.
/// An unrecognized role fails closed (`SelectorUnsupported`), never
/// silently ignored.
#[cfg(windows)]
fn role_to_control_type(role: &str) -> Option<UIA_CONTROLTYPE_ID> {
    use windows::Win32::UI::Accessibility::{
        UIA_ButtonControlTypeId, UIA_CheckBoxControlTypeId, UIA_ComboBoxControlTypeId,
        UIA_DocumentControlTypeId, UIA_EditControlTypeId, UIA_HyperlinkControlTypeId,
        UIA_ImageControlTypeId, UIA_ListControlTypeId, UIA_ListItemControlTypeId,
        UIA_MenuItemControlTypeId, UIA_PaneControlTypeId, UIA_RadioButtonControlTypeId,
        UIA_TextControlTypeId,
    };
    match role.to_ascii_lowercase().as_str() {
        "button" => Some(UIA_ButtonControlTypeId),
        "link" | "hyperlink" => Some(UIA_HyperlinkControlTypeId),
        "edit" | "textbox" | "input" => Some(UIA_EditControlTypeId),
        "text" => Some(UIA_TextControlTypeId),
        "checkbox" => Some(UIA_CheckBoxControlTypeId),
        "combobox" => Some(UIA_ComboBoxControlTypeId),
        "list" => Some(UIA_ListControlTypeId),
        "listitem" => Some(UIA_ListItemControlTypeId),
        "menuitem" => Some(UIA_MenuItemControlTypeId),
        "image" => Some(UIA_ImageControlTypeId),
        "radiobutton" => Some(UIA_RadioButtonControlTypeId),
        "document" => Some(UIA_DocumentControlTypeId),
        "pane" => Some(UIA_PaneControlTypeId),
        _ => None,
    }
}

#[cfg(windows)]
fn control_type_to_string(control_type: Option<UIA_CONTROLTYPE_ID>) -> String {
    match control_type {
        None => "unknown".to_string(),
        Some(ct) => format!("{}", ct.0),
    }
}

/// Resolves exactly one element matching `selector` within `root`'s own
/// subtree, then independently verifies (via a bounded `GetParentElement`
/// ancestor walk -- never a `CurrentProcessId`/`CurrentNativeWindowHandle`
/// equality check against this PROCESS's own identity, which the prior
/// gate's live spike proved does not work against WebView2's real,
/// multi-process architecture) that it terminates at `surface_hwnd`.
#[cfg(windows)]
fn resolve_unique_element(
    automation: &IUIAutomation,
    root: &IUIAutomationElement,
    surface_hwnd: isize,
    selector: &UiaSelectorSpec,
    bounds: &UiaBounds,
) -> Result<IUIAutomationElement, UiaExecutionError> {
    if selector.role.is_none() && selector.accessible_name.is_none() {
        return Err(UiaExecutionError::SelectorUnsupported {
            reason: "a selector must supply role and/or accessible_name -- node_ref-only resolution is not supported"
                .to_string(),
        });
    }
    let control_type = match &selector.role {
        Some(role) => Some(role_to_control_type(role).ok_or_else(|| {
            UiaExecutionError::SelectorUnsupported {
                reason: format!("unrecognized role: {role}"),
            }
        })?),
        None => None,
    };

    let true_condition =
        unsafe { automation.CreateTrueCondition() }.map_err(|e| UiaExecutionError::ComFailure {
            message: e.to_string(),
        })?;
    let all = unsafe { root.FindAll(TreeScope_Descendants, &true_condition) }.map_err(|e| {
        UiaExecutionError::ComFailure {
            message: e.to_string(),
        }
    })?;
    let count = unsafe { all.Length() }
        .unwrap_or(0)
        .min(bounds.max_elements_scanned);

    let mut matches: Vec<IUIAutomationElement> = Vec::new();
    for i in 0..count {
        let Ok(el) = (unsafe { all.GetElement(i) }) else {
            continue;
        };
        if let Some(expected_ct) = control_type {
            match unsafe { el.CurrentControlType() } {
                Ok(actual) if actual == expected_ct => {}
                _ => continue,
            }
        }
        if let Some(expected_name) = &selector.accessible_name {
            match unsafe { el.CurrentName() } {
                Ok(actual) if &actual.to_string() == expected_name => {}
                _ => continue,
            }
        }
        matches.push(el);
        if matches.len() > 1 {
            // Already ambiguous -- no need to keep scanning further, but
            // keep counting via `matches.len()` semantics below by not
            // truncating the loop (a real count still matters for the
            // error message). Continue scanning to report an accurate
            // match_count rather than an artificially-capped one.
        }
    }

    match matches.len() {
        0 => Err(UiaExecutionError::SelectorNotFound),
        1 => {
            let element = matches.into_iter().next().expect("length checked above");
            verify_containment(automation, &element, surface_hwnd, bounds)?;
            Ok(element)
        }
        n => Err(UiaExecutionError::SelectorAmbiguous {
            match_count: n as i32,
        }),
    }
}

#[cfg(windows)]
fn verify_containment(
    automation: &IUIAutomation,
    element: &IUIAutomationElement,
    surface_hwnd: isize,
    bounds: &UiaBounds,
) -> Result<(), UiaExecutionError> {
    let walker =
        unsafe { automation.RawViewWalker() }.map_err(|e| UiaExecutionError::ComFailure {
            message: e.to_string(),
        })?;
    let mut current = element.clone();
    for _ in 0..bounds.max_ancestor_depth {
        let native = unsafe { current.CurrentNativeWindowHandle() }
            .map(|h| h.0 as isize)
            .unwrap_or(0);
        if native == surface_hwnd {
            return Ok(());
        }
        current = match unsafe { walker.GetParentElement(&current) } {
            Ok(parent) => parent,
            Err(_) => return Err(UiaExecutionError::ContainmentFailed),
        };
    }
    Err(UiaExecutionError::ContainmentFailed)
}

#[cfg(not(windows))]
impl UiaWorkerPool {
    pub fn submit(
        &self,
        _surface_hwnd: isize,
        _operation: UiaOperationKind,
        _timeout: Duration,
    ) -> Result<
        (
            u64,
            tokio::sync::oneshot::Receiver<Result<UiaOutcome, UiaExecutionError>>,
        ),
        UiaExecutionError,
    > {
        Err(UiaExecutionError::ComFailure {
            message: "UIA execution is only implemented for the Windows WebView2RuntimeAdapter"
                .to_string(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Deliberately pure bookkeeping tests only -- no real COM/UIA call is
    // exercised here (this crate's own `tauri::test` limitation, disclosed
    // repeatedly elsewhere in this codebase, applies identically to UIA:
    // there is no way to construct a real WebView2 surface in this test
    // binary). What IS provable without a live surface: the pool's own
    // admission/cap/retirement bookkeeping, which never touches COM at all.
    // Real UIA behavior (root resolution, containment, ambiguity, click,
    // type) is proven only by a disposable live spike -- see the
    // implementation report for what was actually run.

    #[test]
    fn pool_starts_with_zero_workers() {
        let pool = UiaWorkerPool::new(4, 100);
        assert_eq!(pool.healthy_worker_count(), 0);
        assert_eq!(pool.total_created_count(), 0);
    }

    #[test]
    fn retiring_an_unknown_slot_id_is_a_no_op() {
        let pool = UiaWorkerPool::new(4, 100);
        pool.retire(999);
        assert_eq!(pool.healthy_worker_count(), 0);
    }

    // -- The tests below DO exercise real Windows COM/UIA (this dev/test
    // machine is genuinely Windows -- `#[cfg(windows)]` is active, not
    // skipped). They deliberately target an unresolvable HWND (raw value
    // `0`) rather than a real WebView2 surface, which this test binary
    // cannot construct (see the module comment above) -- but worker
    // creation itself (`CoInitializeEx`/`CoCreateInstance(CUIAutomation)`)
    // does not depend on the HWND at all (it is only consulted later,
    // inside the worker, when a job actually runs), so these tests prove
    // real pool/worker/cap bookkeeping against a real, live COM apartment
    // and a real OS thread -- not a mock.
    //
    // Every test below acquires `UIA_TEST_SERIALIZE` for its entire body.
    // Production only ever has ONE `UiaWorkerPool` for the whole process
    // (`lib.rs`'s `.setup()`), so the "never concurrently instantiate
    // `IUIAutomation`" guarantee only needs to hold WITHIN a single pool --
    // which `submit`'s own mutex already provides (see the module's own
    // doc comment). But `cargo test`'s default runner executes many `#[test]`
    // functions concurrently on separate OS threads, and each test below
    // constructs its OWN, separate `UiaWorkerPool` -- so without this extra
    // guard, two DIFFERENT tests' pools could each race to create their
    // first worker at the same instant, reproducing the exact cross-
    // instance "first `CoCreateInstance(CUIAutomation)` race" the
    // architecture gate's live spike already found unsafe (see the module's
    // own doc comment), just across pool instances instead of within one.
    // A live regression run of exactly this scenario crashed the whole test
    // binary with `STATUS_ACCESS_VIOLATION` before this guard was added --
    // this is a real, observed hazard, not a hypothetical one. This mutex
    // makes the test binary match production's own single-pool topology,
    // never weakening what's actually being asserted.
    #[cfg(windows)]
    static UIA_TEST_SERIALIZE: Mutex<()> = Mutex::new(());

    #[cfg(windows)]
    #[test]
    fn submit_against_an_unresolvable_hwnd_fails_closed_never_panics() {
        let _guard = UIA_TEST_SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        let pool = UiaWorkerPool::new(4, 100);
        let (_slot_id, rx) = pool
            .submit(0, UiaOperationKind::Read, Duration::from_secs(2))
            .expect("worker creation itself does not depend on the HWND");
        let outcome = rx
            .blocking_recv()
            .expect("worker must respond, not panic or hang");
        // Either typed failure is an acceptable, disclosed "fail closed"
        // outcome for a handle that resolves to no real window -- this test
        // asserts closed-ness, not which specific variant fires (that
        // detail is UIA-implementation-defined, not part of this module's
        // own contract).
        assert!(matches!(
            outcome,
            Err(UiaExecutionError::RootUnavailable) | Err(UiaExecutionError::AccessibilityNotReady)
        ));
    }

    #[cfg(windows)]
    #[test]
    fn retiring_a_healthy_worker_does_not_refund_its_lifetime_creation_budget() {
        let _guard = UIA_TEST_SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        let pool = UiaWorkerPool::new(4, 100);
        let (slot_id, rx) = pool
            .submit(0, UiaOperationKind::Read, Duration::from_secs(2))
            .expect("first creation succeeds");
        let _ = rx.blocking_recv();
        assert_eq!(pool.total_created_count(), 1);
        pool.retire_and_join_for_test(slot_id);
        assert_eq!(pool.healthy_worker_count(), 0);
        // Still 1 -- an abandoned worker's budget unit is gone permanently,
        // exactly as the module's own doc comment on `max_lifetime_creations`
        // states; `retire` must never look like a refund.
        assert_eq!(pool.total_created_count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn submit_fails_closed_once_the_lifetime_creation_cap_is_exhausted() {
        let _guard = UIA_TEST_SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        let pool = UiaWorkerPool::new(4, 1);
        let (slot_id, rx) = pool
            .submit(0, UiaOperationKind::Read, Duration::from_secs(2))
            .expect("first creation succeeds within the cap");
        let _ = rx.blocking_recv();
        assert_eq!(pool.total_created_count(), 1);
        // Abandon the only worker ever created (as `browser_grant.rs` does
        // on a real timeout) -- the pool now has zero healthy workers but
        // has already spent its entire lifetime creation budget. Uses the
        // test-only join variant (see its own doc comment) since this
        // worker is known-healthy, not suspected-hung.
        pool.retire_and_join_for_test(slot_id);
        assert_eq!(pool.healthy_worker_count(), 0);
        let result = pool.submit(0, UiaOperationKind::Read, Duration::from_secs(2));
        assert!(matches!(result, Err(UiaExecutionError::PoolExhausted)));
        // The cap is a hard, permanent stop -- it must not have silently
        // created a second worker to satisfy this call.
        assert_eq!(pool.total_created_count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn submit_fails_closed_when_max_workers_are_all_busy() {
        let _guard = UIA_TEST_SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        let pool = UiaWorkerPool::new(1, 100);
        let (slot_id, rx) = pool
            .submit(0, UiaOperationKind::Read, Duration::from_secs(2))
            .expect("first creation succeeds");
        // Wait for the real job to finish so the worker genuinely exists
        // and is idle, then deterministically force it back to busy --
        // avoiding a flaky race against how fast a real UIA call against an
        // unresolvable HWND actually completes (see the pool's own
        // `mark_slot_busy_for_test` doc comment).
        let _ = rx.blocking_recv();
        pool.mark_slot_busy_for_test(slot_id);
        let result = pool.submit(0, UiaOperationKind::Read, Duration::from_secs(2));
        assert!(matches!(result, Err(UiaExecutionError::PoolExhausted)));
        // Still only the one real worker -- exhaustion must not have
        // silently created a second one past `max_workers`.
        assert_eq!(pool.total_created_count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn concurrent_submissions_never_panic_and_respect_the_worker_cap() {
        // Directly targets the live-spike-confirmed first-instantiation
        // `CoCreateInstance(CUIAutomation)` race: many real OS threads call
        // `submit` on a cold (zero-worker) pool at close to the same
        // instant. If serialization via the pool's own mutex ever slipped,
        // this would manifest as a spurious `ComFailure` (the empirical
        // `E_FAIL` this whole design exists to prevent) rather than a clean
        // `Ok` or `PoolExhausted`.
        let _guard = UIA_TEST_SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        let pool = Arc::new(UiaWorkerPool::new(3, 100));
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let pool = pool.clone();
                std::thread::spawn(move || {
                    pool.submit(0, UiaOperationKind::Read, Duration::from_secs(2))
                })
            })
            .collect();
        let mut ok_count = 0;
        let mut exhausted_count = 0;
        for handle in handles {
            match handle.join().expect("submit must not panic on any thread") {
                Ok((_, rx)) => {
                    let _ = rx
                        .blocking_recv()
                        .expect("worker must respond, not panic or hang");
                    ok_count += 1;
                }
                Err(UiaExecutionError::PoolExhausted) => exhausted_count += 1,
                Err(other) => panic!("unexpected error under concurrent submission: {other:?}"),
            }
        }
        assert_eq!(ok_count + exhausted_count, 8);
        assert!(pool.healthy_worker_count() <= 3);
        assert!(pool.total_created_count() <= 3);
    }
}
