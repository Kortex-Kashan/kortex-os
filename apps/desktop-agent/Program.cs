// KORTEX Windows Desktop Agent (Phase 5).
//
// Runs as a LocalSystem Windows Service. Its entire job in Phase 5 is to prove
// who it is and hold an authenticated session open:
//
//   Machine Installation ID (HKLM)  ->  non-exportable CNG key  ->  CSR
//     ->  trusted HTTPS enrollment  ->  client certificate  ->  mTLS session
//
// It executes nothing. There is deliberately no UI automation, no browser
// driver, no shell, and no process launching in this program, and the protocol
// it speaks has no message that could ask for any. Capability execution is
// Phase 6 and requires its own authorization design.

using System.Net.Http;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using Grpc.Core;
using Grpc.Net.Client;
using Kortex.Agent.V1;
using Microsoft.Win32;

namespace Kortex.Agent;

internal static class Program
{
    private static readonly TimeSpan[] BackoffSchedule =
    {
        TimeSpan.FromSeconds(1),
        TimeSpan.FromSeconds(2),
        TimeSpan.FromSeconds(4),
        TimeSpan.FromSeconds(8),
        TimeSpan.FromSeconds(16),
        TimeSpan.FromSeconds(32),
        TimeSpan.FromSeconds(60),
    };

    private static async Task<int> Main(string[] args)
    {
        using var shutdown = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            shutdown.Cancel();
        };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => shutdown.Cancel();

        try
        {
            var options = AgentOptions.Parse(args);
            var machineId = MachineIdentity.ResolveOrCreate();
            Log($"MachineInstallationId: {machineId}");

            var rootCa = LoadRootCa(options.RootCaPath);

            var certificate = ClientCertificateStore.FindEnrolled(rootCa)
                ?? await EnrollAsync(options, machineId, rootCa, shutdown.Token).ConfigureAwait(false);

            Log($"Client certificate: CN={certificate.GetNameInfo(X509NameType.SimpleName, false)}");

            await RunSessionLoopAsync(options, machineId, rootCa, certificate, shutdown.Token)
                .ConfigureAwait(false);
            return 0;
        }
        catch (OperationCanceledException)
        {
            Log("Shutdown requested; exiting cleanly.");
            return 0;
        }
        catch (AgentFatalException ex)
        {
            // Fail closed and stay failed. Every condition that reaches here is
            // one where continuing would mean operating without a verified
            // identity or without a verified server.
            Log($"FATAL: {ex.Message}");
            return 1;
        }
    }

    // -- Trust bootstrap -----------------------------------------------------

    /// <summary>
    /// Loads the KORTEX Root CA from disk. The agent never downloads a trust
    /// root, never falls back to the machine's public CA store, and never
    /// trusts a certificate on first use.
    /// </summary>
    private static X509Certificate2 LoadRootCa(string path)
    {
        if (!File.Exists(path))
        {
            throw new AgentFatalException(
                $"KORTEX Root CA not found at '{path}'. The installer must place it before the service starts. " +
                "The agent will not download a trust root or fall back to the public CA store.");
        }

        try
        {
            return new X509Certificate2(path);
        }
        catch (CryptographicException ex)
        {
            throw new AgentFatalException($"KORTEX Root CA at '{path}' is not a valid certificate: {ex.Message}");
        }
    }

    // -- Enrollment ----------------------------------------------------------

    private static async Task<X509Certificate2> EnrollAsync(
        AgentOptions options,
        string machineId,
        X509Certificate2 rootCa,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(options.EnrollmentToken))
        {
            throw new AgentFatalException(
                "No enrolled client certificate was found and no --token was supplied. " +
                "Enrollment requires a single-use token issued by an administrator.");
        }

        Log("No client certificate found; enrolling.");

        // Generated locally in Windows CNG and marked non-exportable, so the
        // OS itself refuses to hand the key material to any process, including
        // this one. The key never leaves this machine; only the CSR does.
        using var key = NonExportableKey.Create();
        var request = new CertificateRequest(
            $"CN={machineId}",
            key,
            HashAlgorithmName.SHA256,
            RSASignaturePadding.Pkcs1);

        var csrPem = PemEncode("CERTIFICATE REQUEST", request.CreateSigningRequest());

        using var handler = new HttpClientHandler
        {
            // Pinned to the KORTEX Root CA. This callback is strictly more
            // restrictive than the default: it additionally requires the chain
            // to terminate at our own root. It never returns true on error.
            ServerCertificateCustomValidationCallback = (_, serverCert, chain, errors) =>
                ServerTrust.Validate(serverCert, chain, errors, rootCa),
        };

        using var http = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(30) };

        var payload = JsonSerializer.Serialize(new
        {
            token = options.EnrollmentToken,
            csr = csrPem,
            machine_installation_id = machineId,
        });

        using var content = new StringContent(payload, Encoding.UTF8, "application/json");
        using var response = await http
            .PostAsync(new Uri($"{options.BackendBaseUrl}/api/v1/agent/enroll"), content, cancellationToken)
            .ConfigureAwait(false);

        if (!response.IsSuccessStatusCode)
        {
            // The response body is deliberately not echoed into the log: it is
            // an error message from a server we have authenticated, but the
            // request we sent contained the enrollment token and we keep the
            // two well apart.
            throw new AgentFatalException($"Enrollment was refused by the backend (HTTP {(int)response.StatusCode}).");
        }

        var certificatePem = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        var issued = X509Certificate2.CreateFromPem(certificatePem);

        // Bind the issued certificate to the non-exportable private key that
        // is already in the store, then persist it. `CopyWithPrivateKey`
        // produces an ephemeral association; importing it into LocalMachine\My
        // is what makes the pairing durable across restarts.
        using var withKey = issued.CopyWithPrivateKey(key);
        var persistable = new X509Certificate2(
            withKey.Export(X509ContentType.Pfx),
            (string?)null,
            ClientCertificateStore.ProductionKeyStorageFlags);

        using var store = new X509Store(
            ClientCertificateStore.ProductionStoreName,
            ClientCertificateStore.ProductionStoreLocation);
        store.Open(OpenFlags.ReadWrite);
        store.Add(persistable);
        store.Close();

        Log("Enrollment complete; certificate imported into LocalMachine\\My.");
        return persistable;
    }

    // -- Session loop --------------------------------------------------------

    private static async Task RunSessionLoopAsync(
        AgentOptions options,
        string machineId,
        X509Certificate2 rootCa,
        X509Certificate2 clientCertificate,
        CancellationToken cancellationToken)
    {
        var attempt = 0;

        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await RunOneSessionAsync(options, machineId, rootCa, clientCertificate, cancellationToken)
                    .ConfigureAwait(false);
                attempt = 0; // a clean session resets the backoff
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (RpcException ex) when (ex.StatusCode == StatusCode.Unauthenticated)
            {
                // The backend has revoked or disabled this identity. Retrying
                // cannot fix that, and hammering the Gateway with a rejected
                // certificate is exactly what a compromised agent would do.
                throw new AgentFatalException(
                    "The Gateway rejected this agent's identity as disabled or revoked. Re-enrollment is required.");
            }
            catch (RpcException ex)
            {
                Log($"Session ended ({ex.StatusCode}); reconnecting.");
            }

            var delay = BackoffSchedule[Math.Min(attempt, BackoffSchedule.Length - 1)];
            attempt++;
            await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
        }
    }

    private static async Task RunOneSessionAsync(
        AgentOptions options,
        string machineId,
        X509Certificate2 rootCa,
        X509Certificate2 clientCertificate,
        CancellationToken cancellationToken)
    {
        var handler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                ClientCertificates = new X509CertificateCollection { clientCertificate },
                RemoteCertificateValidationCallback = (_, serverCert, chain, errors) =>
                    ServerTrust.Validate(serverCert as X509Certificate2, chain, errors, rootCa),
            },
            KeepAlivePingDelay = TimeSpan.FromSeconds(30),
            KeepAlivePingTimeout = TimeSpan.FromSeconds(10),
        };

        using var channel = GrpcChannel.ForAddress(
            options.GatewayAddress,
            new GrpcChannelOptions { HttpHandler = handler });

        var client = new DesktopAgentGateway.DesktopAgentGatewayClient(channel);
        using var call = client.Connect(cancellationToken: cancellationToken);

        await call.RequestStream.WriteAsync(new AgentMessage
        {
            Status = new AgentStatus
            {
                MachineInstallationId = machineId,
                Status = "ONLINE",
            },
        }).ConfigureAwait(false);

        await foreach (var message in call.ResponseStream.ReadAllAsync(cancellationToken).ConfigureAwait(false))
        {
            if (message.PayloadCase == GatewayMessage.PayloadOneofCase.SessionAck)
            {
                Log($"Session established: {message.SessionAck.SessionId}");
            }
        }

        await call.RequestStream.CompleteAsync().ConfigureAwait(false);
    }

    private static string PemEncode(string label, byte[] der)
    {
        var builder = new StringBuilder();
        builder.Append("-----BEGIN ").Append(label).Append("-----\n");
        builder.Append(Convert.ToBase64String(der, Base64FormattingOptions.InsertLineBreaks).Replace("\r\n", "\n"));
        builder.Append("\n-----END ").Append(label).Append("-----\n");
        return builder.ToString();
    }

    private static void Log(string message) =>
        Console.WriteLine($"[{DateTimeOffset.UtcNow:O}] {message}");
}

/// <summary>Fatal, non-retryable condition. The agent stops rather than degrading.</summary>
internal sealed class AgentFatalException : Exception
{
    public AgentFatalException(string message) : base(message) { }
}

internal sealed record AgentOptions(
    string BackendBaseUrl,
    string GatewayAddress,
    string? EnrollmentToken,
    string RootCaPath)
{
    public static AgentOptions Parse(string[] args)
    {
        string? token = null;
        var backend = "https://gateway.kortex.local:8443";
        var gateway = "https://gateway.kortex.local:50051";
        var rootCa = DefaultRootCaPath;

        for (var i = 0; i < args.Length - 1; i++)
        {
            switch (args[i])
            {
                case "--token":
                    token = args[i + 1];
                    break;
                case "--backend":
                    backend = args[i + 1];
                    break;
                case "--gateway":
                    gateway = args[i + 1];
                    break;
                case "--root-ca":
                    rootCa = args[i + 1];
                    break;
            }
        }

        return new AgentOptions(backend.TrimEnd('/'), gateway, token, rootCa);
    }

    public const string DefaultRootCaPath = @"C:\Program Files\KORTEX\Agent\certs\kortex_root_ca.crt";
}

/// <summary>Resolves the persistent Machine Installation ID from HKLM.</summary>
internal static class MachineIdentity
{
    private const string RegistryKeyPath = @"SOFTWARE\KORTEX\Agent";
    private const string ValueName = "MachineInstallationId";

    /// <summary>
    /// Reads the Machine Installation ID, generating one only on a genuinely
    /// unenrolled installation.
    ///
    /// The order matters. If the id is missing or malformed but a KORTEX client
    /// certificate already exists in the store, this machine has been enrolled
    /// before and something has removed or corrupted its identity. Generating a
    /// replacement there would silently mint a new installation identity for an
    /// already-enrolled machine, so that case fails closed and demands explicit
    /// re-enrollment instead.
    /// </summary>
    public static string ResolveOrCreate()
    {
        using var key = Registry.LocalMachine.CreateSubKey(RegistryKeyPath, writable: true)
            ?? throw new AgentFatalException($@"Cannot open HKLM\{RegistryKeyPath}. The agent must run as LocalSystem.");

        var existing = key.GetValue(ValueName) as string;
        if (!string.IsNullOrWhiteSpace(existing) && Guid.TryParse(existing, out var parsed))
        {
            return parsed.ToString("D");
        }

        if (ClientCertificateStore.AnyKortexCertificateExists())
        {
            throw new AgentFatalException(
                "The MachineInstallationId is missing or malformed, but this installation already holds a KORTEX " +
                "client certificate. Refusing to generate a replacement identity for an already-enrolled machine. " +
                "Explicit re-enrollment is required.");
        }

        var generated = Guid.NewGuid().ToString("D");
        key.SetValue(ValueName, generated, RegistryValueKind.String);
        return generated;
    }
}

/// <summary>Creates RSA keys that Windows itself refuses to export.</summary>
internal static class NonExportableKey
{
    public const int KeySizeBits = 2048;

    /// <summary>
    /// Builds the exact CNG creation parameters the agent uses.
    ///
    /// Split out from <see cref="Create"/> only so tests can assert what is
    /// requested without needing the elevated token that machine-scoped key
    /// creation requires. The production path below passes this same object
    /// straight to <c>CngKey.Create</c>, so there is no second configuration
    /// that could drift from it.
    /// </summary>
    public static CngKeyCreationParameters BuildCreationParameters()
    {
        var parameters = new CngKeyCreationParameters
        {
            // The load-bearing line. With ExportPolicy left at its default,
            // the key could be exported by any process running as this user;
            // `None` makes the OS refuse, so a stolen key cannot be copied off
            // the machine even by code running as LocalSystem.
            ExportPolicy = CngExportPolicies.None,

            // Machine scope, not user scope: the agent runs as a LocalSystem
            // service with no interactive user profile, and the certificate it
            // binds this key to lives in LocalMachine\My. A user-scoped key
            // would be unreachable from that context and would not survive the
            // absence of a loaded profile.
            //
            // This is also why creating one requires an elevated Administrator
            // or SYSTEM token: the machine key store
            // (%ProgramData%\Microsoft\Crypto\Keys) grants write access only to
            // SYSTEM and Administrators. That ACL is a security property, not
            // an obstacle: it is what stops an unprivileged local process from
            // planting a key the service would later trust.
            KeyCreationOptions = CngKeyCreationOptions.MachineKey,
            Provider = CngProvider.MicrosoftSoftwareKeyStorageProvider,
        };
        parameters.Parameters.Add(
            new CngProperty("Length", BitConverter.GetBytes(KeySizeBits), CngPropertyOptions.None));
        return parameters;
    }

    public static RSA Create()
    {
        var cngKey = CngKey.Create(CngAlgorithm.Rsa, $"KORTEX-Agent-{Guid.NewGuid():N}", BuildCreationParameters());
        return new RSACng(cngKey);
    }
}

/// <summary>Finds the agent's enrolled certificate in LocalMachine\My.</summary>
internal static class ClientCertificateStore
{
    // The store the agent's identity lives in. Named constants rather than
    // inline literals so a single test can pin them: silently switching to
    // CurrentUser would still compile and still appear to work for an
    // interactive developer, while leaving the LocalSystem service unable to
    // find its own certificate at boot.
    public const StoreName ProductionStoreName = StoreName.My;
    public const StoreLocation ProductionStoreLocation = StoreLocation.LocalMachine;

    // Flags used when persisting the issued certificate.
    //
    // `MachineKeySet` keeps the private key in the machine key container, so
    // it survives having no loaded user profile. `PersistKeySet` makes the
    // certificate-to-key association durable across restarts rather than
    // ephemeral. `Exportable` is deliberately absent: adding it would re-open
    // the private key to extraction and defeat the CNG export policy the key
    // was created under.
    public const X509KeyStorageFlags ProductionKeyStorageFlags =
        X509KeyStorageFlags.MachineKeySet | X509KeyStorageFlags.PersistKeySet;

    public static X509Certificate2? FindEnrolled(X509Certificate2 rootCa)
    {
        using var store = new X509Store(
            ClientCertificateStore.ProductionStoreName,
            ClientCertificateStore.ProductionStoreLocation);
        store.Open(OpenFlags.ReadOnly);

        foreach (var candidate in store.Certificates)
        {
            if (!candidate.HasPrivateKey)
            {
                continue;
            }

            if (!string.Equals(candidate.Issuer, rootCa.Subject, StringComparison.Ordinal))
            {
                continue;
            }

            if (candidate.NotAfter <= DateTime.Now)
            {
                continue;
            }

            return candidate;
        }

        return null;
    }

    /// <summary>
    /// Whether any KORTEX-issued certificate exists, valid or expired.
    ///
    /// Deliberately broader than <see cref="FindEnrolled"/>: it answers "has
    /// this machine ever been enrolled", which is what the Machine
    /// Installation ID fail-closed check needs. An expired certificate still
    /// proves prior enrollment.
    /// </summary>
    public static bool AnyKortexCertificateExists()
    {
        using var store = new X509Store(
            ClientCertificateStore.ProductionStoreName,
            ClientCertificateStore.ProductionStoreLocation);
        store.Open(OpenFlags.ReadOnly);

        foreach (var candidate in store.Certificates)
        {
            if (candidate.Issuer.Contains("KORTEX Internal Root CA", StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }
}

/// <summary>Server certificate validation pinned to the KORTEX Root CA.</summary>
internal static class ServerTrust
{
    /// <summary>
    /// Accepts a server certificate only when it chains to the KORTEX Root CA
    /// with no policy errors.
    ///
    /// This never returns true on a validation error. In particular it does
    /// not tolerate <c>RemoteCertificateNameMismatch</c>, so SAN hostname
    /// verification stays in force, and it does not consult the machine's
    /// public CA store: a certificate for the right hostname issued by a
    /// public CA is still rejected.
    /// </summary>
    public static bool Validate(
        X509Certificate2? serverCertificate,
        X509Chain? chain,
        SslPolicyErrors errors,
        X509Certificate2 rootCa)
    {
        if (serverCertificate is null || errors != SslPolicyErrors.None)
        {
            return false;
        }

        using var verification = new X509Chain();
        verification.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
        verification.ChainPolicy.CustomTrustStore.Add(rootCa);
        verification.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck; // Phase 5 defines no CRL/OCSP
        verification.ChainPolicy.VerificationFlags = X509VerificationFlags.NoFlag;

        if (!verification.Build(serverCertificate))
        {
            return false;
        }

        // Confirm the chain actually terminates at our root rather than merely
        // building successfully.
        var root = verification.ChainElements[^1].Certificate;
        return root.Thumbprint == rootCa.Thumbprint;
    }
}
