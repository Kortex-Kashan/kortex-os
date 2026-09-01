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

/// Storage abstraction for a single named key value, mirroring `ipc.rs`'s
/// `TokenStore` trait exactly (same shape, same reason: production code and
/// tests share one code path). `store`/`load` operate on an opaque string
/// value — this module, not the trait, decides that value is `0x`-prefixed
/// hex.
pub trait KeyStore: Send + Sync {
    fn load(&self, user: &str) -> Option<String>;
    fn store(&self, user: &str, value: &str);
}

/// Production implementation: the OS-native credential store, via the same
/// `keyring` crate and service name `ipc.rs::KeyringTokenStore` already
/// uses (a distinct `user` per entry keeps this fully independent of the
/// session-token entry).
pub struct KeyringKeyStore;

impl KeyStore for KeyringKeyStore {
    fn load(&self, user: &str) -> Option<String> {
        keyring::Entry::new(KEYRING_SERVICE, user).ok()?.get_password().ok()
    }

    fn store(&self, user: &str, value: &str) {
        if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, user) {
            // A failed write here means the key is regenerated next launch
            // instead of reused — degraded (sessions won't survive that
            // restart either), never a reason to fail spawning the backend
            // with the freshly generated key we already have in hand.
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
/// a fresh cryptographically random one if none is present *or* the stored
/// value is corrupt/malformed. Corrupt is treated identically to missing —
/// silently truncating/padding an unexpected value would produce a
/// *different* key than whatever the store physically holds, which
/// defeats the entire point of persistence (the backend would still see a
/// key that changed since the last restart).
fn load_or_generate_hex_key(store: &dyn KeyStore, user: &str) -> Result<String, String> {
    if let Some(existing) = store.load(user) {
        if is_valid_hex_key(&existing) {
            return Ok(existing);
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
        fn load(&self, user: &str) -> Option<String> {
            self.values.lock().unwrap().get(user).cloned()
        }

        fn store(&self, user: &str, value: &str) {
            self.values.lock().unwrap().insert(user.to_string(), value.to_string());
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
        assert!(store.load(MASTER_KEY_USER).is_none());

        let key = load_or_generate_hex_key(&store, MASTER_KEY_USER).unwrap();

        assert!(is_valid_hex_key(&key));
        assert_eq!(store.load(MASTER_KEY_USER).as_deref(), Some(key.as_str()));
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
        assert_eq!(store.load(MASTER_KEY_USER).as_deref(), Some(key.as_str()));
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
}
