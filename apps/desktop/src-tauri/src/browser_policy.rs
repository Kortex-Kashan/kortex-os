//! Browser-B4: `BrowserPolicyEngine` — local, synchronous, deterministic
//! navigation policy enforcement.
//!
//! **Why local and synchronous, not routed through the backend's
//! `CapabilityDispatcher`/`SecurityEngine.authorize()`**: the Browser-B4
//! architecture gate found that `browser_security_model.md` §2's original
//! "fifth enforcement point" framing describes a FUTURE state (once B5/B6
//! express browser actions as governed `kortex.browser.*` capabilities
//! reaching `CapabilityDispatcher`), not B4's actual scope. A human click or
//! a page-triggered redirect never produces a capability call at all, and
//! the WebView2 `NavigationStarting` event this module hooks into does not
//! support `GetDeferral` (confirmed against the pinned
//! `webview2-com-sys-0.38.2` bindings) — the allow/deny decision MUST be
//! made synchronously, in-process, with no I/O, or the navigation simply
//! proceeds by default. A backend round-trip is architecturally impossible
//! here, not merely undesirable.
//!
//! **Scope boundary (Browser-B4 V1)**: this module evaluates exactly one
//! question — is the destination scheme/host of a navigation attempt
//! allowed at all — using a single, fixed, global policy (no per-tenant or
//! per-profile variation; see the architecture gate's own explicit V1
//! scope). It does not implement domain allowlisting (no existing KORTEX
//! precedent exists to build one on — confirmed during the gate: the
//! closest-named prior art, `PythonNetworkPolicy.ALLOW_LIST`
//! (`python_exec/models.py`), is a cautionary tale — declared but never
//! actually enforced by its own boundary code). It does not implement
//! popup/download/permission POLICY DECISIONS beyond "deny all" — those are
//! wired in `browser_runtime.rs` directly, reusing this module only for
//! their own denial-audit/event plumbing.
//!
//! **Deliberately profile/tenant-agnostic**: unlike the architecture gate's
//! own tentative sketch (which considered threading `tenant_id`/
//! `profile_id` into `NavigationRequest` "for forward compatibility"),
//! this implementation omits them. Doing otherwise would require crossing
//! the exact boundary Browser-B3 established — `browser_runtime.rs` (where
//! this evaluation must run, inside a raw WebView2 COM callback) has no
//! access to `BrowserProfileStore`'s tenant/profile bindings, and V1's
//! actual decision logic does not use them regardless. A future phase that
//! genuinely needs per-tenant/per-profile navigation policy will need to
//! thread that context in deliberately, not inherit an unused field from
//! this session.
//!
//! **Known limitation, stated plainly**: this policy classifies navigation
//! targets by the LITERAL host string in the URI only — it never performs
//! DNS resolution. It therefore cannot detect DNS-rebinding-style attacks
//! (an attacker-controlled public hostname that resolves to a private IP
//! only at connection time, after WebView2's own network stack looks it
//! up). Closing that gap would require either a network-layer hook this
//! module does not have access to, or an async DNS pre-check — impossible
//! within `NavigationStarting`'s no-deferral, synchronous contract. This is
//! a real, disclosed gap, not a silently-assumed-safe simplification.

use serde::Serialize;

/// A navigation attempt this engine is asked to evaluate. Deliberately
/// carries only what the decision itself needs — see the module's own doc
/// comment on why `tenant_id`/`profile_id` are NOT fields here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NavigationRequest {
    pub uri: String,
    /// From `ICoreWebView2NavigationStartingEventArgs::IsUserInitiated` —
    /// carried for future use (e.g. a later phase distinguishing
    /// human-driven from page-triggered navigation) but NOT read by V1's
    /// actual decision logic below, which applies the identical rule
    /// regardless of trigger source: a dangerous destination is dangerous
    /// whether a human clicked it or a script redirected to it.
    pub is_user_initiated: bool,
    /// From `IsRedirected` — same status as `is_user_initiated` above.
    pub is_redirected: bool,
}

/// Coarse network classification for a denied destination — deliberately
/// coarse (a category, never the raw host/IP) so the audit log and the
/// frontend policy-denied event never carry more than a KORTEX developer
/// or user needs to understand WHY something was blocked, per the
/// explicit "avoid leaking query parameters, tokens, identifiers, or other
/// sensitive data" requirement this phase was authorized under.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum NetworkClassification {
    Loopback,
    PrivateIpv4,
    LinkLocalIpv4,
    ThisNetworkIpv4,
    CarrierGradeNat,
    UniqueLocalIpv6,
    LinkLocalIpv6,
    LocalHostname,
}

/// Every reason `evaluate_navigation` can deny a request. `PrivateNetworkAccess`
/// is a first-class variant (not a sub-case of a generic "denied" reason)
/// per the explicit requirement that it be a first-class B4 policy result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum DenyReason {
    /// The URI could not be parsed as an absolute URL, or an http(s) URL
    /// had no host component at all. Fail-closed: an unparseable
    /// destination is never treated as safe-by-default.
    Malformed,
    SchemeNotAllowed {
        scheme: String,
    },
    PrivateNetworkAccess {
        classification: NetworkClassification,
    },
    /// Popups (`NewWindowRequested`), downloads (`DownloadStarting`), and
    /// native permission requests (`PermissionRequested`) are denied
    /// unconditionally in Browser-B4 V1 — never evaluated by
    /// `evaluate_navigation` above, constructed directly by
    /// `browser_runtime.rs`'s own handlers for these three event kinds. Not
    /// a property of any specific request: V1 simply does not yet implement
    /// a real policy (or the confirmation/deferral UI a real policy would
    /// need) for these action kinds. Named "not yet" rather than "denied"
    /// deliberately, matching the B4 approval decision's own framing of each
    /// of these three as a future capability, not a permanent prohibition —
    /// see `docs/architecture/browser_decision_log.md`.
    NotYetSupported,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PolicyDecision {
    Allow,
    Deny(DenyReason),
}

/// The core B4 V1 policy: only `http`/`https` schemes are potentially
/// allowed; destination safety (local/private-network access) is evaluated
/// separately and independently; anything malformed or unsafe fails
/// closed. Pure, synchronous, and deterministic by construction — the same
/// `NavigationRequest` always produces the same `PolicyDecision`, directly
/// mirroring the parametrized-table test pattern already established for
/// this codebase's RBAC/ABAC evaluators (`backend/tests/unit/
/// test_rbac_abac.py`).
pub fn evaluate_navigation(request: &NavigationRequest) -> PolicyDecision {
    let Ok(parsed) = tauri::Url::parse(&request.uri) else {
        return PolicyDecision::Deny(DenyReason::Malformed);
    };

    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
            scheme: scheme.to_string(),
        });
    }

    // `Url::host()` (not `host_str()`) deliberately: `host_str()` returns
    // an IPv6 literal WRAPPED IN BRACKETS (`"[::1]"`), which
    // `std::net::IpAddr`'s own parser rejects outright — parsing that
    // bracketed string would silently fail and let every IPv6
    // loopback/private/link-local literal fall through as "not
    // recognized", hence "Allow". `Url::host()` returns the already-typed
    // `url::Host` enum (`Domain`/`Ipv4`/`Ipv6`), sidestepping that
    // re-parsing entirely — confirmed by this module's own test suite,
    // which caught the bracket bug in an earlier draft that used
    // `host_str()` directly.
    let Some(host) = parsed.host() else {
        // An http(s) URL with no host at all (malformed for our purposes —
        // `tauri::Url`/`url` normally rejects this at parse time for these
        // schemes, but this is a second, explicit check rather than
        // trusting that invariant silently).
        return PolicyDecision::Deny(DenyReason::Malformed);
    };

    if let Some(classification) = classify_host(&host) {
        return PolicyDecision::Deny(DenyReason::PrivateNetworkAccess { classification });
    }

    PolicyDecision::Allow
}

/// Classifies the parsed URI host (never a re-parsed string — see the
/// module's own doc comment on `Url::host()` vs `host_str()`) as a
/// local/private-network destination, or `None` if it looks like an
/// ordinary public destination. Domain names are never resolved via DNS —
/// see the module's own doc comment on that disclosed limitation.
fn classify_host(host: &url::Host<&str>) -> Option<NetworkClassification> {
    match host {
        url::Host::Domain(domain) => {
            // A trailing dot (`"localhost."`) is DNS's own root-anchored
            // FQDN notation — resolvers (including this platform's) treat
            // it as identical to `"localhost"`, but a plain
            // `eq_ignore_ascii_case` comparison does not, which would have
            // let `http://localhost./` sail through as an ordinary,
            // unrecognized domain (caught by this module's own adversarial
            // test suite, not merely assumed safe). Stripping ALL trailing
            // dots, not just one, stays on the fail-closed side even for a
            // name real DNS would treat as invalid (a double trailing dot
            // creates an empty label) — over-denying a malformed name is
            // safe; under-denying a working alias is not.
            if domain
                .trim_end_matches('.')
                .eq_ignore_ascii_case("localhost")
            {
                Some(NetworkClassification::LocalHostname)
            } else {
                None
            }
        }
        url::Host::Ipv4(ip) => classify_ipv4(*ip),
        url::Host::Ipv6(ip) => classify_ipv6(*ip),
    }
}

/// Explicit octet-range checks rather than relying on `Ipv4Addr`'s own
/// `is_private()`/`is_link_local()` helpers directly in the public API —
/// used internally where convenient, but the ranges themselves are spelled
/// out here so the exact rule set is visible and independently reviewable
/// in one place, matching the deny list this phase was explicitly
/// authorized under (loopback, RFC1918, link-local, "this network",
/// carrier-grade NAT).
fn classify_ipv4(ip: std::net::Ipv4Addr) -> Option<NetworkClassification> {
    let o = ip.octets();
    if ip.is_loopback() {
        return Some(NetworkClassification::Loopback);
    }
    if o[0] == 10 {
        return Some(NetworkClassification::PrivateIpv4);
    }
    if o[0] == 172 && (16..=31).contains(&o[1]) {
        return Some(NetworkClassification::PrivateIpv4);
    }
    if o[0] == 192 && o[1] == 168 {
        return Some(NetworkClassification::PrivateIpv4);
    }
    if o[0] == 169 && o[1] == 254 {
        return Some(NetworkClassification::LinkLocalIpv4);
    }
    if o[0] == 0 {
        return Some(NetworkClassification::ThisNetworkIpv4);
    }
    if o[0] == 100 && (64..=127).contains(&o[1]) {
        return Some(NetworkClassification::CarrierGradeNat);
    }
    None
}

fn classify_ipv6(ip: std::net::Ipv6Addr) -> Option<NetworkClassification> {
    if ip.is_loopback() {
        return Some(NetworkClassification::Loopback);
    }
    // An IPv4-mapped IPv6 literal (`::ffff:a.b.c.d`) must be classified by
    // its embedded IPv4 address, not treated as an ordinary IPv6 host —
    // otherwise `::ffff:127.0.0.1` would sail through as "not private".
    if let Some(v4) = ip.to_ipv4_mapped() {
        return classify_ipv4(v4);
    }
    let seg = ip.segments();
    // Unique local address, fc00::/7.
    if (seg[0] & 0xfe00) == 0xfc00 {
        return Some(NetworkClassification::UniqueLocalIpv6);
    }
    // Link-local, fe80::/10.
    if (seg[0] & 0xffc0) == 0xfe80 {
        return Some(NetworkClassification::LinkLocalIpv6);
    }
    None
}

/// What crosses the Tauri IPC boundary to the frontend, and what's written
/// to the local audit log — deliberately the SAME shape for both, and
/// deliberately minimal: never the full URI, never a path/query/fragment,
/// never anything an attacker-controlled page could have placed a
/// token/identifier into. `surface_id` and `action` alone tell the user
/// which tab and what kind of action was blocked; `reason` tells them
/// (coarsely) why.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PolicyDeniedEvent {
    pub surface_id: String,
    pub action: PolicyAction,
    pub reason: DenyReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum PolicyAction {
    Navigation,
    Popup,
    Download,
    Permission,
}

pub const POLICY_DENIED_EVENT_NAME: &str = "browser://policy-denied";

/// Browser-B4 audit event kinds, appended to the SAME local, structured
/// JSON-Lines log Browser-B3 established (`browser_profile_store.rs`'s
/// `record_audit_event`/`AuditLogEntry` — see D23) — reused for the exact
/// same reason: Browser IPC has no backend hop today, so a new backend
/// capability would prematurely import part of B5's own job.
///
/// **Deliberately NOT tenant/profile-attributed**, unlike
/// `browser_profile_store.rs`'s own audit entries: the enforcement point
/// for these events lives inside `browser_runtime.rs`'s raw WebView2 COM
/// callback, which — preserving Browser-B3's own profile-agnostic
/// boundary for `BrowserRuntime` — has no access to
/// `ActiveProfileSurfaces`'s tenant/profile bindings. `surface_id` alone
/// is the available, honest attribution. A future phase wanting
/// tenant-attributed policy audit entries would need to deliberately
/// thread that lookup into the runtime — not something this phase does
/// merely to make the audit trail superficially richer.
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PolicyAuditEvent {
    NavigationBlocked,
    PopupBlocked,
    DownloadBlocked,
    PermissionDenied,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PolicyAuditLogEntry<'a> {
    event: PolicyAuditEvent,
    timestamp_utc: u64,
    surface_id: &'a str,
    reason: Option<&'a DenyReason>,
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Best-effort, append-only JSON Lines write to `<audit_log_path>` — a
/// logging failure must never block or influence the policy decision it
/// describes, matching `browser_profile_store::record_audit_event`'s own
/// convention exactly (same file, same format, same non-blocking posture).
pub fn record_policy_audit_event(
    audit_log_path: &std::path::Path,
    event: PolicyAuditEvent,
    surface_id: &str,
    reason: Option<&DenyReason>,
) {
    let entry = PolicyAuditLogEntry {
        event,
        timestamp_utc: unix_now(),
        surface_id,
        reason,
    };
    let Ok(line) = serde_json::to_string(&entry) else {
        return;
    };
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(audit_log_path)
    {
        use std::io::Write;
        let _ = writeln!(file, "{line}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(uri: &str) -> NavigationRequest {
        NavigationRequest {
            uri: uri.to_string(),
            is_user_initiated: true,
            is_redirected: false,
        }
    }

    // -- Deterministic parametrized table, mirroring test_rbac_abac.py's
    // own established pattern for this exact kind of policy-decision test.

    #[test]
    fn ordinary_public_https_is_allowed() {
        assert_eq!(
            evaluate_navigation(&request("https://example.com/page?x=1")),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn ordinary_public_http_is_allowed() {
        assert_eq!(
            evaluate_navigation(&request("http://example.com")),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn is_user_initiated_and_is_redirected_never_change_the_decision() {
        let a = PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
            scheme: "file".to_string(),
        });
        let mut r = request("file:///etc/passwd");
        r.is_user_initiated = false;
        r.is_redirected = true;
        assert_eq!(evaluate_navigation(&r), a);
        r.is_user_initiated = true;
        r.is_redirected = false;
        assert_eq!(evaluate_navigation(&r), a);
    }

    #[test]
    fn file_scheme_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("file:///etc/passwd")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "file".to_string()
            })
        );
    }

    #[test]
    fn javascript_scheme_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("javascript:alert(1)")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "javascript".to_string()
            })
        );
    }

    #[test]
    fn data_scheme_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("data:text/html,<script>alert(1)</script>")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "data".to_string()
            })
        );
    }

    #[test]
    fn about_scheme_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("about:blank")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "about".to_string()
            })
        );
    }

    #[test]
    fn blob_scheme_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("blob:https://example.com/uuid")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "blob".to_string()
            })
        );
    }

    #[test]
    fn kortex_auth_custom_scheme_is_denied() {
        // A page must never be able to trigger KORTEX's own OAuth
        // deep-link scheme by navigating a browser surface to it.
        assert_eq!(
            evaluate_navigation(&request("kortex-auth://callback")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "kortex-auth".to_string()
            })
        );
    }

    #[test]
    fn case_varied_scheme_is_still_denied() {
        assert_eq!(
            evaluate_navigation(&request("JAVASCRIPT:alert(1)")),
            PolicyDecision::Deny(DenyReason::SchemeNotAllowed {
                scheme: "javascript".to_string()
            })
        );
    }

    #[test]
    fn unparseable_uri_fails_closed_not_open() {
        assert_eq!(
            evaluate_navigation(&request("not a url at all")),
            PolicyDecision::Deny(DenyReason::Malformed)
        );
    }

    #[test]
    fn empty_uri_fails_closed() {
        assert_eq!(
            evaluate_navigation(&request("")),
            PolicyDecision::Deny(DenyReason::Malformed)
        );
    }

    #[test]
    fn localhost_hostname_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://localhost:8000/health")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::LocalHostname
            })
        );
    }

    #[test]
    fn localhost_hostname_is_denied_case_insensitively() {
        assert_eq!(
            evaluate_navigation(&request("http://LocalHost/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::LocalHostname
            })
        );
    }

    #[test]
    fn localhost_with_trailing_dot_fqdn_notation_is_still_denied() {
        // Adversarial finding: DNS treats a trailing dot as the root
        // anchor, so `"localhost."` resolves identically to `"localhost"`
        // on this platform's own resolver — but a naive exact-string
        // comparison does not treat them as equal. An earlier version of
        // `classify_host` was caught failing this exact test (`Domain(
        // "localhost.")` sailed through as an ordinary, unrecognized
        // domain, i.e. `Allow`), fixed by stripping trailing dots before
        // comparing.
        for candidate in [
            "http://localhost./",
            "http://LOCALHOST./",
            "http://localhost../",
        ] {
            assert_eq!(
                evaluate_navigation(&request(candidate)),
                PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                    classification: NetworkClassification::LocalHostname
                }),
                "expected {candidate} to be denied identically to \"localhost\""
            );
        }
    }

    #[test]
    fn ipv4_loopback_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://127.0.0.1:8000/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::Loopback
            })
        );
    }

    #[test]
    fn ipv4_loopback_full_range_is_denied() {
        for candidate in [
            "http://127.0.0.1/",
            "http://127.1.2.3/",
            "http://127.255.255.255/",
        ] {
            assert_eq!(
                evaluate_navigation(&request(candidate)),
                PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                    classification: NetworkClassification::Loopback
                }),
                "expected {candidate} to be classified as loopback"
            );
        }
    }

    #[test]
    fn ipv6_loopback_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://[::1]/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::Loopback
            })
        );
    }

    #[test]
    fn rfc1918_ranges_are_denied() {
        for candidate in [
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://172.31.255.255/",
            "http://192.168.1.1/",
        ] {
            assert_eq!(
                evaluate_navigation(&request(candidate)),
                PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                    classification: NetworkClassification::PrivateIpv4
                }),
                "expected {candidate} to be classified as private"
            );
        }
    }

    #[test]
    fn rfc1918_boundary_is_precise_172_15_and_172_32_are_public() {
        // 172.16.0.0/12 is 172.16.0.0-172.31.255.255 -- 172.15.x and
        // 172.32.x must NOT be misclassified as private.
        assert_eq!(
            evaluate_navigation(&request("http://172.15.0.1/")),
            PolicyDecision::Allow
        );
        assert_eq!(
            evaluate_navigation(&request("http://172.32.0.1/")),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn alternate_numeric_ipv4_encodings_of_loopback_are_still_denied() {
        // A classic SSRF-filter-bypass class: decimal, hex, octal, and
        // short-form (fewer than 4 dotted parts) integer encodings of an
        // IPv4 address, all parsed by WHATWG's own IPv4 parser (which the
        // `url` crate implements) into the SAME `Ipv4Addr` as the ordinary
        // dotted-quad form. This is safe by construction here — `Url::host()`
        // returns the already-parsed `Ipv4Addr`, and `classify_ipv4` only
        // ever sees that typed value, never the original string — but it is
        // exactly the kind of bypass a naive string-prefix/regex filter
        // would miss, so it is proven here, not merely assumed.
        for candidate in [
            "http://2130706433/",   // decimal
            "http://0x7f000001/",   // hex
            "http://017700000001/", // octal
            "http://127.1/",        // short-form (2 parts)
            "http://0177.0.0.1/",   // octal first octet
            "http://0x7f.0.0.1/",   // hex first octet
        ] {
            assert_eq!(
                evaluate_navigation(&request(candidate)),
                PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                    classification: NetworkClassification::Loopback
                }),
                "expected {candidate} (an alternate encoding of 127.0.0.1) to be denied as loopback"
            );
        }
    }

    #[test]
    fn link_local_ipv4_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://169.254.1.1/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::LinkLocalIpv4
            })
        );
    }

    #[test]
    fn this_network_ipv4_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://0.0.0.1/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::ThisNetworkIpv4
            })
        );
    }

    #[test]
    fn carrier_grade_nat_ipv4_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://100.64.0.1/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::CarrierGradeNat
            })
        );
        // 100.63.x and 100.128.x are public, outside the CGN range.
        assert_eq!(
            evaluate_navigation(&request("http://100.63.0.1/")),
            PolicyDecision::Allow
        );
        assert_eq!(
            evaluate_navigation(&request("http://100.128.0.1/")),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn ipv6_unique_local_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://[fd00::1]/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::UniqueLocalIpv6
            })
        );
    }

    #[test]
    fn ipv6_link_local_is_denied() {
        assert_eq!(
            evaluate_navigation(&request("http://[fe80::1]/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::LinkLocalIpv6
            })
        );
    }

    #[test]
    fn ipv4_mapped_ipv6_loopback_is_denied_not_misclassified_as_public() {
        // The specific bypass this test targets: an IPv4-mapped IPv6
        // literal embedding a loopback address must be classified by its
        // EMBEDDED address, never treated as "just some IPv6 host".
        assert_eq!(
            evaluate_navigation(&request("http://[::ffff:127.0.0.1]/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::Loopback
            })
        );
    }

    #[test]
    fn ipv4_mapped_ipv6_private_is_denied_not_misclassified_as_public() {
        assert_eq!(
            evaluate_navigation(&request("http://[::ffff:10.0.0.1]/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::PrivateIpv4
            })
        );
    }

    #[test]
    fn userinfo_cannot_be_used_to_disguise_the_real_host() {
        // A classic string-matching-filter bypass: putting the "expected"
        // host in the userinfo (`user@host`) component instead of the real
        // host component. Safe here by construction — `Url::host()` reads
        // the real, structurally-parsed host, never the raw string — but
        // proven explicitly rather than left implicit.
        assert_eq!(
            evaluate_navigation(&request("http://example.com@127.0.0.1/")),
            PolicyDecision::Deny(DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::Loopback
            }),
            "the real host (127.0.0.1) must be evaluated, not the userinfo (example.com)"
        );
        assert_eq!(
            evaluate_navigation(&request("http://127.0.0.1@example.com/")),
            PolicyDecision::Allow,
            "the real host (example.com) must be evaluated, not the userinfo (127.0.0.1)"
        );
    }

    #[test]
    fn ordinary_public_ipv4_literal_is_allowed() {
        assert_eq!(
            evaluate_navigation(&request("http://93.184.216.34/")),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn ordinary_public_ipv6_literal_is_allowed() {
        assert_eq!(
            evaluate_navigation(&request("http://[2606:2800:220:1:248:1893:25c8:1946]/")),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn decision_is_deterministic_across_repeated_evaluations() {
        let r = request("http://127.0.0.1/");
        let first = evaluate_navigation(&r);
        for _ in 0..1000 {
            assert_eq!(
                evaluate_navigation(&r),
                first,
                "the same request must always produce the same decision"
            );
        }
    }

    #[test]
    fn policy_denied_event_serializes_without_the_uri() {
        let event = PolicyDeniedEvent {
            surface_id: "browser-surface-abc".to_string(),
            action: PolicyAction::Navigation,
            reason: DenyReason::PrivateNetworkAccess {
                classification: NetworkClassification::Loopback,
            },
        };
        let json = serde_json::to_string(&event).unwrap();
        assert!(
            !json.contains("127.0.0.1"),
            "the event must never carry the actual denied URI/host"
        );
        assert!(json.contains("surfaceId"));
        assert!(json.contains("navigation"));
    }

    #[test]
    fn not_yet_supported_reason_serializes_with_the_expected_kind() {
        for (action, action_json) in [
            (PolicyAction::Popup, "popup"),
            (PolicyAction::Download, "download"),
            (PolicyAction::Permission, "permission"),
        ] {
            let event = PolicyDeniedEvent {
                surface_id: "browser-surface-abc".to_string(),
                action,
                reason: DenyReason::NotYetSupported,
            };
            let json = serde_json::to_string(&event).unwrap();
            assert!(json.contains("\"kind\":\"notYetSupported\""));
            assert!(json.contains(&format!("\"action\":\"{action_json}\"")));
        }
    }

    #[test]
    fn audit_log_entries_never_contain_the_full_uri() {
        let temp = std::env::temp_dir().join(format!("kortex-b4-policy-audit-test-{}", unix_now()));
        let reason = DenyReason::PrivateNetworkAccess {
            classification: NetworkClassification::Loopback,
        };
        record_policy_audit_event(
            &temp,
            PolicyAuditEvent::NavigationBlocked,
            "browser-surface-abc",
            Some(&reason),
        );

        let content = std::fs::read_to_string(&temp).unwrap();
        assert!(content.contains("NAVIGATION_BLOCKED"));
        assert!(content.contains("browser-surface-abc"));
        assert!(!content.contains("127.0.0.1"));
        assert!(!content.contains("http://"));

        std::fs::remove_file(&temp).ok();
    }
}
