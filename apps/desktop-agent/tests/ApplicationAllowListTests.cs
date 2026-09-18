// Phase 6: `kortex.desktop.launch` must never resolve to an arbitrary
// executable. These tests exercise `ApplicationAllowList` in isolation —
// no process is ever launched here.

using Kortex.Agent;
using Xunit;

namespace Kortex.Agent.Tests;

public sealed class ApplicationAllowListTests
{
    [Fact]
    public void MissingFileProducesAnEmptyAllowList()
    {
        var path = Path.Combine(Path.GetTempPath(), $"kortex-allowlist-missing-{Guid.NewGuid():N}.json");
        Assert.False(File.Exists(path));

        var allowList = ApplicationAllowList.LoadFromFile(path);

        Assert.Null(allowList.Resolve("notepad"));
        Assert.Null(allowList.Resolve("anything"));
    }

    [Fact]
    public void ResolvesAnEntryDeclaredInTheFile()
    {
        var path = WriteTempAllowList("""{ "notepad": { "path": "C:\\Windows\\System32\\notepad.exe" } }""");
        try
        {
            var allowList = ApplicationAllowList.LoadFromFile(path);

            var resolved = allowList.Resolve("notepad");

            Assert.NotNull(resolved);
            Assert.Equal(@"C:\Windows\System32\notepad.exe", resolved!.Path);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void UnknownApplicationIdResolvesToNull()
    {
        var path = WriteTempAllowList("""{ "notepad": { "path": "C:\\Windows\\System32\\notepad.exe" } }""");
        try
        {
            var allowList = ApplicationAllowList.LoadFromFile(path);

            // Not just "any unknown id" -- specifically an id that looks like
            // an attempt to launch something else entirely must resolve to
            // nothing, exactly like a typo would.
            Assert.Null(allowList.Resolve("cmd"));
            Assert.Null(allowList.Resolve("powershell"));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void LookupIsCaseInsensitive()
    {
        var path = WriteTempAllowList("""{ "Notepad": { "path": "C:\\Windows\\System32\\notepad.exe" } }""");
        try
        {
            var allowList = ApplicationAllowList.LoadFromFile(path);

            Assert.NotNull(allowList.Resolve("notepad"));
            Assert.NotNull(allowList.Resolve("NOTEPAD"));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void EntryWithNoPathIsIgnoredRatherThanCrashing()
    {
        var path = WriteTempAllowList("""{ "broken": { "path": "" }, "notepad": { "path": "C:\\Windows\\System32\\notepad.exe" } }""");
        try
        {
            var allowList = ApplicationAllowList.LoadFromFile(path);

            Assert.Null(allowList.Resolve("broken"));
            Assert.NotNull(allowList.Resolve("notepad"));
        }
        finally
        {
            File.Delete(path);
        }
    }

    private static string WriteTempAllowList(string json)
    {
        var path = Path.Combine(Path.GetTempPath(), $"kortex-allowlist-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, json);
        return path;
    }
}
