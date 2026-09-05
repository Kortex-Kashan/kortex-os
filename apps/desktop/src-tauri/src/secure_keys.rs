//! M7.1 persistent key material for the backend sidecar's
//! `KORTEX_MASTER_KEY` / `KORTEX_AUTH_SIGNING_PRIVATE_KEY` environment
//! variables.
//!
//! Reuses the exact OS-keyring mechanism `ipc.rs`'s `KeyringTokenStore`
//! already established for the session token — a second, distinct keyring
//! entry per key, behind the same kind of small trait `ipc.rs` uses
//! (`TokenStore`) so the generate-or-reuse logic is unit-testable against
//! an in-memory double rather than the real OS credential store (flaky/
//! permission-sensitive in CI, per `ipc.rs`'s own test module docs — the
//! same reasoning applies here unchanged).
//!
//! Without this module, `backend/src/kortex/api/kernel_bootstrap.py`'s
//! `_resolve_key` falls back to an ephemeral `os.urandom(32)` key every
//! process start — every sidecar restart would silently invalidate every
//! previously-issued session token and every encrypted secret. This module
//! exists so that never happens once a real key has been generated once.

const KEYRING_SERVICE: &str = "kortex-desktop";
const MASTER_KEY_USER: &str = "backend-master-key";
const SIGNING_KEY_USER: &str = "backend-signing-key";
const KEY_LENGTH_BYTES: usize = 32;

pub const MASTER_KEY_ENV_VAR: &str = "KORTEX_MASTER_KEY";
pub const SIGNING_KEY_ENV_VAR: &str = "KORTEX_AUTH_SIGNING_PRIVATE_KEY";

/// Outcome of attempting to read a stored key. Distinguishes "the store
/// was queried and confirmed no entry exists" (`ConfirmedAbsent` — a
/// genuine first run, safe to generate a fresh key) from "the store could
/// not be queried at all" (`Unreadable` — keyring daemon absent, access
/// denied, ...). These are deliberately NOT the same case
/// (implementation_plan.md Part 2, Control 4): collapsing them, as this
/// module previously did, means a machine with a temporarily-unavailable
/// keyring silently generates a *replacement* cryptographic identity with
/// no way to tell that a prior one may have existed — every session and
/// every previously-encrypted secret invalidated with zero diagnostic
/// trace. `Unreadable` must fail closed instead.
pub enum KeyLoadResult {
    Found(String),
    ConfirmedAbsent,
    Unreadable(String),
}

/// Storage abstraction for a single named key value, mirroring `ipc.rs`'s
/// `TokenStore` trait exactly (same shape, same reason: production code and
/// tests share one code path). `store`/`load` operate on an opaque string
/// value — this module, not the trait, decides that value is `0x`-prefixed
/// hex.
pub trait KeyStore: Send + Sync {
    fn load(&self, user: &str) -> KeyLoadResult;
    fn store(&self, user: &str, value: &str);
}

/// Production implementation: the OS-native credential store, via the same
/// `keyring` crate and service name `ipc.rs::KeyringTokenStore` already
/// uses (a distinct `user` per entry keeps this fully independent of the
/// session-token entry).
pub struct KeyringKeyStore;

impl KeyStore for KeyringKeyStore {
    fn load(&self, user: &str) -> KeyLoadResult {
        let entry = match keyring::Entry::new(KEYRING_SERVICE, user) {
            Ok(entry) => entry,
            Err(e) => return KeyLoadResult::Unreadable(e.to_string()),
        };
        match entry.get_password() {
            Ok(value) => KeyLoadResult::Found(value),
            // `NoEntry` is the one keyring::Error variant that unambiguously
            // means "the credential store was reached and confirmed empty" —
            // every other variant (PlatformFailure, NoStorageAccess, ...)
            // means the store itself could not be queried, which is the
            // ambiguous case Control 4 requires failing closed on.
            Err(keyring::Error::NoEntry) => KeyLoadResult::ConfirmedAbsent,
            Err(e) => KeyLoadResult::Unreadable(e.to_string()),
        }
    }

    fn store(&self, user: &str, value: &str) {
        if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, user) {
            // A failed write here means the key is regenerated next launch
            // instead of reused — degraded (sessions won't survive that
            // restart either), never a reason to fail spawning the backend
            // with the freshly generated key we already have in hand. This
            // path is only reached after `load` has already confirmed no
            // prior identity exists (or was unreadably ambiguous, in which
            // case we never get here at all — see `load_or_generate_hex_key`),
            // so a failed write here can never silently discard a real prior
            // key: there wasn't one to discard.
            let _ = entry.set_password(value);
        }
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn hex_decode(hex: &str) -> Result<Vec<u8>, ()> {
    if hex.len() % 2 != 0 {
        return Err(());
    }
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).map_err(|_| ()))
        .collect()
}

/// Whether `value` is a well-formed `0x`-prefixed key of exactly
/// `KEY_LENGTH_BYTES` bytes — the exact shape
/// `kernel_bootstrap.py::_resolve_key` decodes via `bytes.fromhex(raw[2:])`.
fn is_valid_hex_key(value: &str) -> bool {
    value
        .strip_prefix("0x")
        .and_then(|hex| hex_decode(hex).ok())
        .map(|decoded| decoded.len() == KEY_LENGTH_BYTES)
        .unwrap_or(false)
}

/// Loads a 32-byte key from `store` under `user`, generating and persisting
/// a fresh cryptographically random one only when it is safe to do so:
/// the value is corrupt/malformed (still confirmed *present*, so there is
/// no ambiguity — silently truncating/padding it would produce a
/// *different* key than whatever the store physically holds, defeating
/// the point of persistence, so a malformed value is treated as needing
/// regeneration exactly as before), or the store confirms no entry has
/// ever existed (`ConfirmedAbsent` — a genuine first run).
///
/// **Fails closed (Control 4)** when the store itself could not be queried
/// (`Unreadable` — keyring daemon absent, access denied, ...): whether this
/// is a first install or a machine with an existing identity we simply
/// cannot read is genuinely undetermined in that case, and silently
/// generating a new key risks discarding a real prior one with no trace.
/// The caller (`resolve_backend_sidecar_config`) already surfaces any `Err`
/// here as a backend-spawn failure, routing to the existing
/// backend-unavailable recovery UX (`BackendUnavailableScreen.tsx`) —
/// no new UI is introduced by this behavior.
fn load_or_generate_hex_key(store: &dyn KeyStore, user: &str) -> Result<String, String> {
    match store.load(user) {
        KeyLoadResult::Found(existing) if is_valid_hex_key(&existing) => return Ok(existing),
        KeyLoadResult::Found(_) => {
            // Present but malformed: not ambiguous, safe to regenerate.
        }
        KeyLoadResult::ConfirmedAbsent => {
            // Genuine first run: no prior identity exists to protect.
        }
        KeyLoadResult::Unreadable(reason) => {
            return Err(format!(
                "cannot confirm whether a persistent key already exists for '{user}' ({reason}); \
                 refusing to generate a replacement key, which could silently discard an \
                 existing cryptographic identity. Restore OS keyring access and restart."
            ));
        }
    }

    let mut bytes = [0u8; KEY_LENGTH_BYTES];
    getrandom::getrandom(&mut bytes).map_err(|e| e.to_string())?;
    let hex_key = format!("0x{}", hex_encode(&bytes));

    store.store(user, &hex_key);
    Ok(hex_key)
}

/// The two backend key environment variables, loaded or generated-and-
/// persisted as needed. Returns `Err` only if the platform CSPRNG itself is
/// unavailable — the caller (`sidecar.rs`'s spawn resolution) treats that
/// as a spawn failure, exactly like any other inability to construct a
/// valid `SidecarConfig`. A keyring read/write failure is never fatal here
/// (see `KeyringKeyStore::store`'s doc) — only key *generation* can fail.
pub fn load_or_generate_backend_keys(store: &dyn KeyStore) -> Result<Vec<(String, String)>, String> {
    let master_key = load_or_generate_hex_key(store, MASTER_KEY_USER)?;
    let signing_key = load_or_generate_hex_key(store, SIGNING_KEY_USER)?;
    Ok(vec![
        (MASTER_KEY_ENV_VAR.to_string(), master_key),
        (SIGNING_KEY_ENV_VAR.to_string(), signing_key),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    #[derive(Default)]
    struct MemoryKeyStore {
        values: Mutex<HashMap<String, String>>,
    }

    impl KeyStore for MemoryKeyStore {
        fn load(&self, user: &str) -> KeyLoadResult {
            match self.values.lock().unwrap().get(user).cloned() {
                Some(value) => KeyLoadResult::Found(value),
                None => KeyLoadResult::ConfirmedAbsent,
            }
        }

        fn store(&self, user: &str, value: &str) {
            self.values.lock().unwrap().insert(user.to_string(), value.to_string());
        }
    }

    /// A store that always reports "unreadable" — simulates a keyring
    /// daemon that is absent/access-denied, as opposed to one that has been
    /// queried and confirmed empty.
    struct UnreadableKeyStore;

    impl KeyStore for UnreadableKeyStore {
        fn load(&self, _user: &str) -> KeyLoadResult {
            KeyLoadResult::Unreadable("simulated keyring access failure".to_string())
        }

        fn store(&self, _user: &str, _value: &str) {
            panic!("store() must never be called when load() could not confirm absence");
        }
    }

    #[test]
    fn hex_encode_decode_round_trips() {
        let bytes = [0u8, 1, 255, 16, 32];
        let encoded = hex_encode(&bytes);
        assert_eq!(encoded, "0001ff1020");
        assert_eq!(hex_decode(&encoded).unwrap(), bytes.to_vec());
    }

    #[test]
    fn is_valid_hex_key_accepts_only_a_well_formed_32_byte_value() {
        let good = format!("0x{}", hex_encode(&[0u8; KEY_LENGTH_BYTES]));
        assert!(is_valid_hex_key(&good));
        assert!(!is_valid_hex_key("0xnothex"));
        assert!(!is_valid_hex_key(&format!("0x{}", hex_encode(&[0u8; 16]))), "wrong length must be rejected");
        assert!(!is_valid_hex_key("deadbeef"), "missing 0x prefix must be rejected");
    }

    #[test]
    fn first_call_generates_a_valid_key_and_persists_it() {
        let store = MemoryKeyStore::default();
        assert!(matches!(store.load(MASTER_KEY_USER), KeyLoadResult::ConfirmedAbsent));

        let key = load_or_generate_hex_key(&store, MASTER_KEY_USER).unwrap();

        assert!(is_valid_hex_key(&key));
        assert!(matches!(store.load(MASTER_KEY_USER), KeyLoadResult::Found(v) if v == key));
    }

    #[test]
    fn a_simulated_restart_reuses_the_exact_same_key_instead_of_regenerating() {
        // The store is not reset between these two calls — exactly what a
        // real OS keyring entry looks like across two process launches.
        let store = MemoryKeyStore::default();
        let first_launch = load_or_generate_hex_key(&store, MASTER_KEY_USER).unwrap();
        let second_launch = load_or_generate_hex_key(&store, MASTER_KEY_USER).unwrap();
        assert_eq!(first_launch, second_launch);
    }

    #[test]
    fn a_corrupt_stored_value_triggers_regeneration_not_a_crash() {
        let store = MemoryKeyStore::default();
        store.store(MASTER_KEY_USER, "not-a-valid-hex-key");

        let key = load_or_generate_hex_key(&store, MASTER_KEY_USER).unwrap();

        assert!(is_valid_hex_key(&key));
        assert!(matches!(store.load(MASTER_KEY_USER), KeyLoadResult::Found(v) if v == key));
    }

    #[test]
    fn first_install_with_confirmed_absent_entry_generates_a_key() {
        // Genuine first run: the store is queried and confirms no entry
        // exists (not merely "unreadable") -- safe to generate.
        let store = MemoryKeyStore::default();
        let key = load_or_generate_hex_key(&store, MASTER_KEY_USER).unwrap();
        assert!(is_valid_hex_key(&key));
    }

    #[test]
    fn unreadable_keyring_fails_closed_instead_of_silently_replacing_identity() {
        // Control 4: an unreadable store (keyring daemon absent, access
        // denied, ...) must never be treated the same as "confirmed no
        // entry" -- a prior identity might exist and be unreadable, and
        // silently generating a replacement would discard it with no
        // trace. `UnreadableKeyStore::store` panics if ever called, so this
        // test also proves no write is attempted on this path.
        let store = UnreadableKeyStore;
        let result = load_or_generate_hex_key(&store, MASTER_KEY_USER);
        assert!(result.is_err(), "an unreadable keyring must fail closed, not generate a key");
    }

    #[test]
    fn master_and_signing_keys_are_distinct_and_independently_persisted() {
        let store = MemoryKeyStore::default();
        let pairs = load_or_generate_backend_keys(&store).unwrap();

        assert_eq!(pairs.len(), 2);
        let master = pairs.iter().find(|(k, _)| k == MASTER_KEY_ENV_VAR).unwrap();
        let signing = pairs.iter().find(|(k, _)| k == SIGNING_KEY_ENV_VAR).unwrap();
        assert_ne!(master.1, signing.1, "the two keys must never collide");
        assert!(is_valid_hex_key(&master.1));
        assert!(is_valid_hex_key(&signing.1));

        // A second call (simulated restart) reuses both, unchanged.
        let pairs_again = load_or_generate_backend_keys(&store).unwrap();
        assert_eq!(pairs, pairs_again);
    }

    // --- Real Windows Credential Manager integration test -----------------
    //
    // Every test above proves this module's generate-or-reuse *logic* against
    // an in-memory double -- deliberately, per this file's own module doc,
    // since the real credential store is flaky/permission-sensitive in CI.
    // That leaves an actual gap: nothing proves the crate dependency this
    // logic sits on top of (`keyring`) is configured to use a real OS-backed
    // store at all. It previously wasn't -- `keyring = "3"` with no platform
    // feature resolves to the crate's own in-memory mock on every platform,
    // including Windows, which no unit test against `MemoryKeyStore` could
    // ever catch (confirmed directly: a value stored via that configuration
    // was unreadable, "ConfirmedAbsent", from a second process). This test
    // exercises the real, resolved `keyring::Entry` -- the same crate/version
    // /feature set `KeyringKeyStore` and `ipc.rs`'s `KeyringTokenStore` call
    // -- across genuine OS process boundaries, which an in-process test
    // cannot do: a single test function cannot observe whether a value
    // survived past its own process's lifetime, since the mock backend
    // *also* holds values in memory for as long as the process runs.
    //
    // Deliberately does NOT go through `KeyringKeyStore` itself: that struct
    // hardcodes the real production service/user identifiers
    // (`KEYRING_SERVICE`/`MASTER_KEY_USER`), and this test must never read,
    // write, or collide with an actual developer's or CI runner's real
    // stored keys. It calls `keyring::Entry` directly, under a distinct,
    // disposable service name -- proving the same dependency configuration
    // works, without touching production identifiers or requiring any
    // change to `KeyringKeyStore` itself.
    //
    // `#[ignore]`-gated: this touches a real OS credential store, which is
    // inappropriate for a default `cargo test --lib` run on a developer
    // machine or the Linux `rust` CI job (no Windows Credential Manager
    // exists there). Desktop CI's `windows-installer` job invokes this exact
    // test by name, explicitly, via `-- --ignored`.
    #[cfg(windows)]
    mod real_windows_keyring_integration {
        const TEST_SERVICE: &str = "kortex-desktop-REAL-KEYRING-INTEGRATION-TEST-DISPOSABLE";
        const TEST_USER: &str = "integration-test-account";

        fn entry() -> keyring::Entry {
            keyring::Entry::new(TEST_SERVICE, TEST_USER).expect("failed to construct a keyring Entry")
        }

        /// Best-effort cleanup, safe to call whether or not a credential
        /// exists (mirrors `KeyringTokenStore::clear`'s own reasoning:
        /// "already gone" is an acceptable outcome, never a reason to panic
        /// during cleanup).
        fn cleanup() {
            let _ = entry().delete_credential();
        }

        #[test]
        #[ignore = "touches the real Windows Credential Manager; run explicitly in CI's windows-installer job"]
        fn real_windows_keyring_persists_across_process_restart() {
            cleanup(); // Ensure a clean slate even if a prior run was interrupted.

            // CASE B (ConfirmedAbsent): nothing has been stored yet.
            match entry().get_password() {
                Err(keyring::Error::NoEntry) => {}
                Ok(_) => panic!("test credential unexpectedly already present -- environment not clean"),
                Err(e) => panic!("keyring unreadable before the test even began: {e}"),
            }

            // A disposable, process-local test value -- never a real secret,
            // never printed. Unique per run so a prior interrupted run's
            // leftover value (if cleanup failed) cannot produce a false pass.
            let test_value = format!(
                "kortex-real-keyring-test-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            );

            // Store in THIS process.
            entry().set_password(&test_value).expect("set_password failed against the real credential store");

            // CASE A (Found), proven across a genuine OS process boundary --
            // not merely a second call in this same process, which the mock
            // backend would also satisfy. Re-invokes this same compiled test
            // binary, selecting only the child-store-confirmation helper
            // below, as a brand-new OS process with no shared memory.
            let read_back = run_child_process("secure_keys::tests::real_windows_keyring_integration::read_and_print_length");
            assert!(
                read_back.status.success(),
                "child process could not read back the stored credential: stderr={}",
                String::from_utf8_lossy(&read_back.stderr)
            );
            let stdout = String::from_utf8_lossy(&read_back.stdout);
            assert!(
                stdout.contains(&format!("LEN={}", test_value.len())),
                "child process reported a different length than what was stored (value did not survive the process boundary intact); child stdout: {stdout}"
            );

            // A second, independent process restart reads the identical
            // value again -- proves stability, not a one-shot fluke.
            let read_again = run_child_process("secure_keys::tests::real_windows_keyring_integration::read_and_print_length");
            assert!(read_again.status.success());
            let stdout_again = String::from_utf8_lossy(&read_again.stdout);
            assert!(stdout_again.contains(&format!("LEN={}", test_value.len())));

            // CASE C is proven separately below (does not require a stored
            // value at all -- covered before this cleanup ever runs).

            cleanup();

            // Confirmed absence after deletion -- never "unreadable".
            match entry().get_password() {
                Err(keyring::Error::NoEntry) => {}
                other => panic!("credential should be confirmed absent after cleanup, got {other:?}"),
            }
        }

        /// Not a real test case -- a child-process helper `#[ignore]`d test,
        /// selected by exact name from a *different* process via
        /// `run_child_process`. Reads whatever is currently stored under
        /// `TEST_SERVICE`/`TEST_USER` and prints only its length, never its
        /// content, to stdout for the parent process to assert against.
        #[test]
        #[ignore = "not a standalone test; invoked as a child process by real_windows_keyring_persists_across_process_restart"]
        fn read_and_print_length() {
            match entry().get_password() {
                Ok(value) => println!("LEN={}", value.len()),
                Err(e) => panic!("child process could not read the credential: {e}"),
            }
        }

        fn run_child_process(exact_test_name: &str) -> std::process::Output {
            std::process::Command::new(std::env::current_exe().expect("could not resolve the current test binary's path"))
                .args(["--exact", exact_test_name, "--ignored", "--test-threads=1", "--nocapture"])
                .output()
                .expect("failed to spawn child test process")
        }
    }
}
