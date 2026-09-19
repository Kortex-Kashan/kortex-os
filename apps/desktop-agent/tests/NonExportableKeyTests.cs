// Adversarial matrix test 23: the agent's private key must be non-exportable.
//
// The property has two separable halves, and this file is structured around
// that split because only one of them can be exercised without privilege:
//
//   1. Non-exportability enforcement -- a CNG key created with
//      `ExportPolicy = CngExportPolicies.None` must refuse every private-key
//      export path while remaining usable for signing. This is enforced by the
//      key handle's export policy and is independent of where the key is
//      stored, so it is verified here unconditionally, at runtime, against a
//      real key from the real provider.
//
//   2. Machine-scope storage -- the production key is created with
//      `CngKeyCreationOptions.MachineKey` so a LocalSystem service with no
//      interactive profile can reach it. Creating one requires an elevated
//      Administrator or SYSTEM token, because the machine key store
//      (%ProgramData%\Microsoft\Crypto\Keys) grants write access only to
//      SYSTEM and Administrators. Tests that exercise the real
//      `NonExportableKey.Create()` therefore carry [RequiresElevationFact] and
//      report as skipped, with the reason, when that token is absent.
//
// That ACL is itself a security control -- it is what prevents an
// unprivileged local process from planting a key the service would later
// trust -- so these tests never attempt to work around it, and the
// implementation is never downgraded to user scope to make them run.

using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using Kortex.Agent;
using Xunit;

namespace Kortex.Agent.Tests;

/// <summary>
/// Whether this process can create machine-scoped CNG keys.
/// </summary>
[SupportedOSPlatform("windows")]
internal static class PrivilegeContext
{
    // Both computed in a static constructor rather than via field
    // initializers: initializers run in declaration order, so a `Description`
    // initializer would run after `Detect()` and overwrite what it recorded.
    public static bool CanCreateMachineScopedKeys { get; }

    public static string Description { get; }

    static PrivilegeContext()
    {
        (CanCreateMachineScopedKeys, Description) = Detect();
    }

    private static (bool Elevated, string Description) Detect()
    {
        using var identity = WindowsIdentity.GetCurrent();
        var principal = new WindowsPrincipal(identity);

        // Checked by SID rather than by role name. `IsInRole` returns false
        // when the Administrators SID is present but marked "use for deny
        // only", which is exactly the UAC-filtered token case this needs to
        // detect -- membership in the group is not the same as holding its
        // privileges.
        var administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        var elevated = principal.IsInRole(administrators);
        var description =
            $"identity='{identity.Name}', IsSystem={identity.IsSystem}, ElevatedAdministrator={elevated}";
        return (identity.IsSystem || elevated, description);
    }
}

/// <summary>
/// A Fact that reports as skipped, with a precise reason, when the process
/// lacks the token required to create machine-scoped CNG keys.
///
/// Skipped rather than failed on purpose: a failure asserts the code is
/// wrong, and here the code is not wrong -- the runner simply is not
/// privileged. Reporting it as a failure would be a false defect report, and
/// reporting it as a pass would be a false security claim.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class RequiresElevationFactAttribute : FactAttribute
{
    public RequiresElevationFactAttribute()
    {
        if (!PrivilegeContext.CanCreateMachineScopedKeys)
        {
            Skip =
                "Requires an elevated Administrator or SYSTEM token: machine-scoped CNG key creation "
                + "writes to %ProgramData%\\Microsoft\\Crypto\\Keys, which grants write access only to "
                + "SYSTEM and BUILTIN\\Administrators. Current context: "
                + PrivilegeContext.Description
                + ". Run this suite from an elevated shell, or as the LocalSystem service, to exercise it.";
        }
    }
}

[SupportedOSPlatform("windows")]
public sealed class NonExportableKeyTests
{
    /// <summary>
    /// Creates a key using the production creation parameters, but in user
    /// scope, so the export-policy half of the property can be exercised
    /// without an elevated token.
    ///
    /// Everything except <see cref="CngKeyCreationOptions.MachineKey"/> is
    /// taken verbatim from <see cref="NonExportableKey.BuildCreationParameters"/>,
    /// so the export policy, provider and key length under test are the real
    /// ones. The scope substitution is confined to this helper and is never
    /// applied to the production path.
    /// </summary>
    private static CngKey CreateUserScopedWithProductionPolicy()
    {
        var production = NonExportableKey.BuildCreationParameters();
        var parameters = new CngKeyCreationParameters
        {
            ExportPolicy = production.ExportPolicy,
            Provider = production.Provider,
            KeyCreationOptions = CngKeyCreationOptions.None,
        };
        parameters.Parameters.Add(
            new CngProperty("Length", BitConverter.GetBytes(NonExportableKey.KeySizeBits), CngPropertyOptions.None));
        return CngKey.Create(CngAlgorithm.Rsa, $"KORTEX-Test-{Guid.NewGuid():N}", parameters);
    }

    // -- The production request itself (runs unprivileged) --------------------

    [Fact]
    public void ProductionParametersRequestNonExportableMachineScopedRsa2048()
    {
        // Pins exactly what the agent asks the OS for. This runs without
        // privilege, so the intent stays covered even where the machine key
        // store cannot be written.
        var parameters = NonExportableKey.BuildCreationParameters();

        Assert.Equal(CngExportPolicies.None, parameters.ExportPolicy);
        Assert.Equal(CngKeyCreationOptions.MachineKey, parameters.KeyCreationOptions);
        Assert.Equal(
            CngProvider.MicrosoftSoftwareKeyStorageProvider.Provider,
            parameters.Provider!.Provider);

        var length = Assert.Single(parameters.Parameters, p => p.Name == "Length");
        Assert.Equal(2048, BitConverter.ToInt32(length.GetValue()!, 0));
        Assert.Equal(2048, NonExportableKey.KeySizeBits);
    }

    [Fact]
    public void ProductionParametersNeverAllowExport()
    {
        // Guards the single most important line against a future edit: any
        // export policy other than None -- including the "allow encrypted
        // export only" variants -- must fail this.
        var parameters = NonExportableKey.BuildCreationParameters();

        Assert.True(parameters.ExportPolicy.HasValue, "ExportPolicy must be set explicitly, never left to the default.");
        var policy = parameters.ExportPolicy.Value;

        Assert.Equal(CngExportPolicies.None, policy);
        Assert.False(policy.HasFlag(CngExportPolicies.AllowExport));
        Assert.False(policy.HasFlag(CngExportPolicies.AllowPlaintextExport));
        Assert.False(policy.HasFlag(CngExportPolicies.AllowArchiving));
        Assert.False(policy.HasFlag(CngExportPolicies.AllowPlaintextArchiving));
    }

    // -- Export enforcement, verified at runtime (runs unprivileged) ----------

    [Fact]
    public void ExportPolicyNoneIsHonouredByTheProvider()
    {
        using var key = CreateUserScopedWithProductionPolicy();

        Assert.Equal(CngExportPolicies.None, key.ExportPolicy);
        Assert.Equal(CngAlgorithm.Rsa.Algorithm, key.Algorithm.Algorithm);
        Assert.Equal(
            CngProvider.MicrosoftSoftwareKeyStorageProvider.Provider,
            key.Provider!.Provider);

        try
        {
            using var rsa = new RSACng(key);
            Assert.Equal(2048, rsa.KeySize);
        }
        finally
        {
            key.Delete();
        }
    }

    [Fact]
    public void EveryPrivateKeyExportPathIsRefused()
    {
        // The actual security assertion, against a real provider-backed key:
        // every documented route to the private material must throw. Each is a
        // distinct CNG code path, so a policy that blocked only the obvious one
        // would still leave the others open.
        var key = CreateUserScopedWithProductionPolicy();
        try
        {
            using var rsa = new RSACng(key);

            Assert.Throws<CryptographicException>(() => rsa.ExportParameters(includePrivateParameters: true));
            Assert.Throws<CryptographicException>(() => rsa.ExportPkcs8PrivateKey());
            Assert.Throws<CryptographicException>(() => rsa.ExportRSAPrivateKey());
            Assert.Throws<CryptographicException>(() => rsa.ExportEncryptedPkcs8PrivateKey(
                "not-a-real-password",
                new PbeParameters(PbeEncryptionAlgorithm.Aes256Cbc, HashAlgorithmName.SHA256, 100_000)));

            // Below the RSA abstraction, straight at the CNG handle.
            Assert.Throws<CryptographicException>(() => key.Export(CngKeyBlobFormat.GenericPrivateBlob));
            Assert.Throws<CryptographicException>(() => key.Export(CngKeyBlobFormat.Pkcs8PrivateBlob));
        }
        finally
        {
            key.Delete();
        }
    }

    [Fact]
    public void KeyRemainsUsableForItsIntendedPurpose()
    {
        // Non-exportable must not mean inert. Without this, the assertions
        // above would pass for a key that simply does not work, and the agent
        // could not sign its own CSR.
        var key = CreateUserScopedWithProductionPolicy();
        try
        {
            using var rsa = new RSACng(key);

            var publicKey = rsa.ExportSubjectPublicKeyInfo();
            Assert.NotEmpty(publicKey);

            var data = new byte[] { 1, 2, 3, 4, 5 };
            var signature = rsa.SignData(data, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            Assert.True(rsa.VerifyData(data, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1));
        }
        finally
        {
            key.Delete();
        }
    }


    // -- Certificate store placement (runs unprivileged) ----------------------

    [Fact]
    public void ProductionCertificateStoreIsLocalMachineNeverCurrentUser()
    {
        // The agent runs as LocalSystem, which has no interactive user
        // profile. A CurrentUser store would still compile, and would even
        // appear to work when a developer runs the agent interactively, but
        // the service would find no certificate at boot and would re-enroll
        // forever. Pinned here so that regression fails a test rather than
        // shipping.
        Assert.Equal(StoreLocation.LocalMachine, ClientCertificateStore.ProductionStoreLocation);
        Assert.NotEqual(StoreLocation.CurrentUser, ClientCertificateStore.ProductionStoreLocation);
        Assert.Equal(StoreName.My, ClientCertificateStore.ProductionStoreName);
    }

    [Fact]
    public void ProductionKeyStorageFlagsAreMachineScopedAndNotExportable()
    {
        var flags = ClientCertificateStore.ProductionKeyStorageFlags;

        // Machine-scoped and durable.
        Assert.True(flags.HasFlag(X509KeyStorageFlags.MachineKeySet));
        Assert.True(flags.HasFlag(X509KeyStorageFlags.PersistKeySet));

        // Never exportable: this flag would re-open the private key to
        // extraction at import time, defeating the CNG export policy the key
        // was created under. Its absence is the assertion.
        Assert.False(flags.HasFlag(X509KeyStorageFlags.Exportable));

        // And never user-scoped.
        Assert.False(flags.HasFlag(X509KeyStorageFlags.UserKeySet));
    }

    [Fact]
    public void AgentSourceNeverReferencesCurrentUserStoreOrExportableFlag()
    {
        // A structural sweep of the shipped agent source, so a future edit
        // that reintroduces a CurrentUser store or an Exportable import
        // anywhere -- not just behind the constants above -- is caught.
        var source = ReadAgentSource();

        Assert.DoesNotContain("StoreLocation.CurrentUser", source, StringComparison.Ordinal);
        Assert.DoesNotContain("X509KeyStorageFlags.Exportable", source, StringComparison.Ordinal);
        Assert.DoesNotContain("CngExportPolicies.AllowExport", source, StringComparison.Ordinal);
        Assert.DoesNotContain("CngExportPolicies.AllowPlaintextExport", source, StringComparison.Ordinal);

        // Sanity: prove the sweep actually read the real file rather than an
        // empty string, which would make every assertion above vacuous.
        Assert.Contains("StoreLocation.LocalMachine", source, StringComparison.Ordinal);
        Assert.Contains("CngExportPolicies.None", source, StringComparison.Ordinal);
    }

    [Fact]
    public void AgentHasNoMachineToUserScopeFallback()
    {
        // The failure mode this guards against is a well-intentioned future
        // edit: wrapping key creation in a catch and "helpfully" retrying in
        // user scope when the machine store is not writable. That would turn a
        // loud, correct fail-closed into a silent downgrade -- the agent would
        // appear to enroll successfully while placing its identity somewhere
        // the LocalSystem service can never reach, and somewhere an
        // unprivileged local process can.
        //
        // Asserting on the count, not merely the presence, of the scope
        // option: exactly one occurrence, and it must be MachineKey.
        var source = ReadAgentSource();

        var occurrences = System.Text.RegularExpressions.Regex.Matches(source, @"CngKeyCreationOptions\.\w+");
        Assert.Single(occurrences);
        Assert.Equal("CngKeyCreationOptions.MachineKey", occurrences[0].Value);

        // And the production creation call is not swallowed by a catch that
        // could substitute a different key.
        Assert.DoesNotContain("catch (CryptographicException) { return", source, StringComparison.Ordinal);
    }

    private static string ReadAgentSource()
    {
        var directory = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && directory is not null; i++)
        {
            var candidate = Path.Combine(directory, "Program.cs");
            if (File.Exists(candidate))
            {
                return File.ReadAllText(candidate);
            }
            directory = Path.GetDirectoryName(directory.TrimEnd(Path.DirectorySeparatorChar));
        }
        throw new InvalidOperationException("Could not locate the agent's Program.cs for structural inspection.");
    }

    // -- The real production path (requires elevation) ------------------------

    [RequiresElevationFact]
    public void ProductionKeyIsMachineScopedRsa2048()
    {
        using var rsa = NonExportableKey.Create();
        var cng = Assert.IsType<RSACng>(rsa);

        Assert.Equal(2048, rsa.KeySize);
        Assert.Equal(CngExportPolicies.None, cng.Key.ExportPolicy);
        Assert.True(cng.Key.IsMachineKey);
        cng.Key.Delete();
    }

    [RequiresElevationFact]
    public void ProductionKeyRefusesEveryPrivateKeyExportPath()
    {
        using var rsa = NonExportableKey.Create();
        var cng = Assert.IsType<RSACng>(rsa);
        try
        {
            Assert.Throws<CryptographicException>(() => rsa.ExportParameters(includePrivateParameters: true));
            Assert.Throws<CryptographicException>(() => rsa.ExportPkcs8PrivateKey());
            Assert.Throws<CryptographicException>(() => rsa.ExportRSAPrivateKey());
            Assert.Throws<CryptographicException>(() => rsa.ExportEncryptedPkcs8PrivateKey(
                "not-a-real-password",
                new PbeParameters(PbeEncryptionAlgorithm.Aes256Cbc, HashAlgorithmName.SHA256, 100_000)));
            Assert.Throws<CryptographicException>(() => cng.Key.Export(CngKeyBlobFormat.GenericPrivateBlob));
            Assert.Throws<CryptographicException>(() => cng.Key.Export(CngKeyBlobFormat.Pkcs8PrivateBlob));
        }
        finally
        {
            cng.Key.Delete();
        }
    }

    [RequiresElevationFact]
    public void ProductionKeyRemainsUsableForSigning()
    {
        using var rsa = NonExportableKey.Create();
        var cng = Assert.IsType<RSACng>(rsa);
        try
        {
            var data = new byte[] { 9, 8, 7, 6, 5 };
            var signature = rsa.SignData(data, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            Assert.True(rsa.VerifyData(data, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1));
            Assert.NotEmpty(rsa.ExportSubjectPublicKeyInfo());
        }
        finally
        {
            cng.Key.Delete();
        }
    }
}
