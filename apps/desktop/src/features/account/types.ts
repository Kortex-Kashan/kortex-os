export interface ChangePasswordInput {
  currentPassword: string;
  newPassword: string;
}

export interface SetEmailInput {
  email: string;
}

/** Phase A: admin-provisioned "Register" — the tenant comes exclusively
 * from the caller's own authenticated session server-side, never a field
 * here (see `backend/src/kortex/engines/security/engine.py`'s
 * `register_principal_capability`). */
export interface RegisterPrincipalInput {
  principalId: string;
  password: string;
  roles: string[];
  email?: string;
}
