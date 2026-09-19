// KORTEX Desktop Agent (Phase 6) — application allow-list.
//
// `kortex.desktop.launch` never accepts a filesystem path or command line
// from the Gateway — the wire contract (`DesktopLaunchCommand.application_id`)
// has no field for one at all. This class is the only place a launch request
// is turned into an actual executable path, and it is a closed, local,
// operator-managed mapping: an `application_id` the operator did not list
// here has no way to run anything, no matter what a compromised or malicious
// Gateway asks for.

using System.Text.Json;

namespace Kortex.Agent;

internal sealed record AllowedApplication(string Path, IReadOnlyList<string>? AllowedArguments = null);

internal sealed class ApplicationAllowList
{
    private readonly IReadOnlyDictionary<string, AllowedApplication> _applications;

    private ApplicationAllowList(IReadOnlyDictionary<string, AllowedApplication> applications)
    {
        _applications = applications;
    }

    /// <summary>
    /// Loads the allow-list from disk. A missing file is not an error — it
    /// is an empty allow-list, and every `kortex.desktop.launch` request
    /// then fails closed with `APPLICATION_NOT_ALLOWED` rather than the
    /// agent refusing to start over an optional piece of configuration.
    /// </summary>
    public static ApplicationAllowList LoadFromFile(string path)
    {
        if (!File.Exists(path))
        {
            return new ApplicationAllowList(new Dictionary<string, AllowedApplication>());
        }

        var json = File.ReadAllText(path);
        var raw = JsonSerializer.Deserialize<Dictionary<string, RawEntry>>(
            json,
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? new();

        var applications = new Dictionary<string, AllowedApplication>(StringComparer.OrdinalIgnoreCase);
        foreach (var (applicationId, entry) in raw)
        {
            if (string.IsNullOrWhiteSpace(entry.Path))
            {
                continue;
            }
            applications[applicationId] = new AllowedApplication(entry.Path, entry.AllowedArguments);
        }
        return new ApplicationAllowList(applications);
    }

    /// <summary>
    /// Resolves `applicationId` to its allow-listed executable, or `null` if
    /// it is not present. Deliberately returns the same "not found" outcome
    /// whether the key is unknown or simply unset — there is nothing further
    /// to distinguish, and no caller-visible enumeration of valid ids.
    /// </summary>
    public AllowedApplication? Resolve(string applicationId)
    {
        return _applications.TryGetValue(applicationId, out var application) ? application : null;
    }

    private sealed class RawEntry
    {
        public string Path { get; set; } = string.Empty;

        public List<string>? AllowedArguments { get; set; }
    }
}
