// Phase 7 — genuine Windows E2E for native UI automation.
//
// No mocks anywhere in this file: `KortexAutomationTestApp.exe` is a real
// Win32 process, launched by the real `Application.Launch` FlaUI calls
// inside `DesktopAutomationHandler.Handle(DesktopLaunchCommand)`, and every
// interaction below goes through the real UI Automation tree via UIA3 — the
// exact same handler code path the Desktop Agent runs in production when a
// command arrives over the mTLS session. Nothing here is Tauri/shell
// verification; this is real Win32 UI automation, end to end:
//
//   launch (real process) -> discover UI (real UIA tree) -> type "7", "8"
//   -> click Multiply (real click, real WinForms event handler runs)
//   -> read the freshly computed "56" back from a real label
//   -> unauthorized-equivalent operation (unknown application_id,
//      ambiguous/missing selector) fails closed
//
// proving the calculation is genuinely computed by the driven application,
// not merely echoed back unchanged.

using Kortex.Agent;
using Kortex.Agent.V1;
using Xunit;

namespace Kortex.Agent.Tests;

internal static class TestAppLocator
{
    /// <summary>
    /// Finds the freshly built `KortexAutomationTestApp.exe`, searching both
    /// Debug and Release outputs. Returns `null` — never throws — when it
    /// cannot be found, so a test using it can report a clear `Skip` reason
    /// instead of an opaque failure when the sibling project has not been
    /// built.
    /// </summary>
    public static string? Find()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && directory.Name != "desktop-agent")
        {
            directory = directory.Parent;
        }
        if (directory is null)
        {
            return null;
        }

        var binRoot = Path.Combine(directory.FullName, "testapp", "bin");
        if (!Directory.Exists(binRoot))
        {
            return null;
        }

        return Directory.EnumerateFiles(binRoot, "KortexAutomationTestApp.exe", SearchOption.AllDirectories)
            .OrderByDescending(File.GetLastWriteTimeUtc)
            .FirstOrDefault();
    }
}

/// <summary>
/// Skips with a clear reason when `KortexAutomationTestApp.exe` has not
/// been built yet, mirroring `RequiresElevationFactAttribute`'s established
/// pattern (`NonExportableKeyTests.cs`) rather than adding a new test
/// dependency for conditional skipping.
/// </summary>
public sealed class RequiresTestAppFactAttribute : FactAttribute
{
    public RequiresTestAppFactAttribute()
    {
        if (TestAppLocator.Find() is null)
        {
            Skip = "KortexAutomationTestApp.exe was not found under apps/desktop-agent/testapp/bin. "
                + "Build apps/desktop-agent/testapp/KortexAutomationTestApp.csproj first.";
        }
    }
}

public sealed class DesktopAutomationHandlerE2ETests : IDisposable
{
    private readonly List<string> _log = new();
    private readonly DesktopAutomationHandler _handler;
    private readonly string _appPath;

    public DesktopAutomationHandlerE2ETests()
    {
        _appPath = TestAppLocator.Find() ?? string.Empty;
        var allowListPath = Path.Combine(Path.GetTempPath(), $"kortex-e2e-allowlist-{Guid.NewGuid():N}.json");
        if (_appPath.Length > 0)
        {
            File.WriteAllText(
                allowListPath,
                $$"""{ "kortex-test-app": { "path": {{System.Text.Json.JsonSerializer.Serialize(_appPath)}} } }""");
        }
        _handler = new DesktopAutomationHandler(ApplicationAllowList.LoadFromFile(allowListPath), _log.Add);
    }

    [RequiresTestAppFact]
    public void RealMultiplicationRoundTripThroughLaunchTypeClickReadText()
    {
        var launch = _handler.Handle(new DesktopLaunchCommand { CommandId = "e2e-launch", ApplicationId = "kortex-test-app", TimeoutSeconds = 15 });
        Assert.True(launch.Success, $"launch failed: {launch.ErrorCode} {launch.ErrorMessage}");
        Assert.NotEmpty(launch.WindowHandle);
        Assert.True(launch.ProcessId > 0);

        try
        {
            var typeA = _handler.Handle(new DesktopTypeCommand
            {
                CommandId = "e2e-type-a",
                WindowHandle = launch.WindowHandle,
                Selector = new UiElementSelector { AutomationId = "InputA" },
                Text = "7",
            });
            Assert.True(typeA.Success, $"type A failed: {typeA.ErrorCode} {typeA.ErrorMessage}");

            var typeB = _handler.Handle(new DesktopTypeCommand
            {
                CommandId = "e2e-type-b",
                WindowHandle = launch.WindowHandle,
                Selector = new UiElementSelector { AutomationId = "InputB" },
                Text = "8",
            });
            Assert.True(typeB.Success, $"type B failed: {typeB.ErrorCode} {typeB.ErrorMessage}");

            var click = _handler.Handle(new DesktopClickCommand
            {
                CommandId = "e2e-click",
                WindowHandle = launch.WindowHandle,
                Selector = new UiElementSelector { AutomationId = "MultiplyButton" },
            });
            Assert.True(click.Success, $"click failed: {click.ErrorCode} {click.ErrorMessage}");

            var read = _handler.Handle(new DesktopReadTextCommand
            {
                CommandId = "e2e-read",
                WindowHandle = launch.WindowHandle,
                Selector = new UiElementSelector { AutomationId = "ResultLabel" },
                MaxLength = 32,
            });
            Assert.True(read.Success, $"read failed: {read.ErrorCode} {read.ErrorMessage}");

            // 7 x 8 = 56, computed by the real application's own Click
            // handler -- not asserted against an input we supplied.
            Assert.Equal("56", read.Text);
            Assert.False(read.Truncated);
        }
        finally
        {
            _handler.Dispose();
        }
    }

    [RequiresTestAppFact]
    public void UnauthorizedEquivalentOperationsFailClosed()
    {
        // "Unauthorized equivalent operation" at this layer: an
        // application_id absent from the allow-list, and a UI target that
        // does not exist -- both must fail closed with a decoded error, not
        // launch/interact with anything.
        var disallowedLaunch = _handler.Handle(new DesktopLaunchCommand { CommandId = "e2e-bad-app", ApplicationId = "calc" });
        Assert.False(disallowedLaunch.Success);
        Assert.Equal("APPLICATION_NOT_ALLOWED", disallowedLaunch.ErrorCode);
        Assert.Empty(disallowedLaunch.WindowHandle);

        var launch = _handler.Handle(new DesktopLaunchCommand { CommandId = "e2e-launch-2", ApplicationId = "kortex-test-app", TimeoutSeconds = 15 });
        Assert.True(launch.Success);
        try
        {
            var missingWindow = _handler.Handle(new DesktopClickCommand
            {
                CommandId = "e2e-missing-window",
                WindowHandle = "not-a-real-handle",
                Selector = new UiElementSelector { AutomationId = "MultiplyButton" },
            });
            Assert.False(missingWindow.Success);
            Assert.Equal("WINDOW_NOT_FOUND", missingWindow.ErrorCode);

            var missingElement = _handler.Handle(new DesktopClickCommand
            {
                CommandId = "e2e-missing-element",
                WindowHandle = launch.WindowHandle,
                Selector = new UiElementSelector { AutomationId = "DoesNotExist" },
            });
            Assert.False(missingElement.Success);
            Assert.Equal("ELEMENT_NOT_FOUND", missingElement.ErrorCode);
        }
        finally
        {
            _handler.Dispose();
        }
    }

    [RequiresTestAppFact]
    public void AmbiguousSelectorFailsClosedRatherThanPickingOneElement()
    {
        var launch = _handler.Handle(new DesktopLaunchCommand { CommandId = "e2e-launch-3", ApplicationId = "kortex-test-app", TimeoutSeconds = 15 });
        Assert.True(launch.Success);
        try
        {
            // ControlType "Edit" alone matches both InputA and InputB -- a
            // real ambiguous match against the real UIA tree, not a
            // fake-agent-reported string.
            var ambiguous = _handler.Handle(new DesktopClickCommand
            {
                CommandId = "e2e-ambiguous",
                WindowHandle = launch.WindowHandle,
                Selector = new UiElementSelector { ControlType = "Edit" },
            });
            Assert.False(ambiguous.Success);
            Assert.Equal("ELEMENT_AMBIGUOUS", ambiguous.ErrorCode);
        }
        finally
        {
            _handler.Dispose();
        }
    }

    [RequiresTestAppFact]
    public void KilledProcessMakesItsWindowHandleStale()
    {
        var launch = _handler.Handle(new DesktopLaunchCommand { CommandId = "e2e-launch-4", ApplicationId = "kortex-test-app", TimeoutSeconds = 15 });
        Assert.True(launch.Success);

        // Simulate the launched application crashing or being closed
        // outside the agent's control -- a real killed process, not a
        // simulated flag.
        using var process = System.Diagnostics.Process.GetProcessById(launch.ProcessId);
        process.Kill();
        process.WaitForExit(5000);

        var afterCrash = _handler.Handle(new DesktopClickCommand
        {
            CommandId = "e2e-stale-window",
            WindowHandle = launch.WindowHandle,
            Selector = new UiElementSelector { AutomationId = "MultiplyButton" },
        });
        Assert.False(afterCrash.Success);
        Assert.Equal("WINDOW_NOT_FOUND", afterCrash.ErrorCode);
    }

    [RequiresTestAppFact]
    public void LaunchFailureOfAnAllowListedButMissingExecutableFailsClosed()
    {
        var brokenAllowListPath = Path.Combine(Path.GetTempPath(), $"kortex-e2e-broken-allowlist-{Guid.NewGuid():N}.json");
        var missingExePath = Path.Combine(Path.GetTempPath(), $"kortex-does-not-exist-{Guid.NewGuid():N}.exe");
        File.WriteAllText(
            brokenAllowListPath,
            $$"""{ "broken-app": { "path": {{System.Text.Json.JsonSerializer.Serialize(missingExePath)}} } }""");

        using var handler = new DesktopAutomationHandler(ApplicationAllowList.LoadFromFile(brokenAllowListPath), _log.Add);
        var launch = handler.Handle(new DesktopLaunchCommand { CommandId = "e2e-launch-failure", ApplicationId = "broken-app", TimeoutSeconds = 5 });

        Assert.False(launch.Success);
        Assert.Equal("LAUNCH_FAILED", launch.ErrorCode);
        Assert.Empty(launch.WindowHandle);
    }

    public void Dispose()
    {
        _handler.Dispose();
    }
}
