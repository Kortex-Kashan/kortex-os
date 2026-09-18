// KORTEX Desktop Agent (Phase 6) — native Windows UI automation execution.
//
// The only code in this project that touches FlaUI/UI Automation, and the
// only code that ever launches a process. Both are deliberately narrow:
//
//   * `kortex.desktop.launch` resolves `application_id` through
//     `ApplicationAllowList` — never a path or command line the Gateway
//     supplied — and refuses anything not on it.
//   * `kortex.desktop.click`/`.type`/`.read_text` resolve a UI element
//     through `UiElementSelector` (AutomationId/Name/ControlType) scoped to
//     one previously launched window. Zero matches or more than one match
//     is rejected outright — this handler never guesses which element a
//     caller meant.
//
// No method here accepts screen coordinates, a shell command, a script, or
// an arbitrary executable path. That is not an omission to fill in later —
// it is the whole security property this file exists to hold.

using System.Collections.Concurrent;
using System.Diagnostics;
using FlaUI.Core.AutomationElements;
using FlaUI.Core.Conditions;
using FlaUI.Core.Input;
using FlaUI.UIA3;
using Kortex.Agent.V1;
using Application = FlaUI.Core.Application;

namespace Kortex.Agent;

internal sealed class DesktopAutomationHandler : IDisposable
{
    // A window whose application has exited is only ever noticed reactively
    // today, when some later command happens to reference that exact
    // `windowHandle` again (see `ResolveElement`). A window nobody ever
    // queries again would otherwise hold its `Application`/`Window`
    // references (and the underlying process handle) for the life of the
    // agent process. `SweepExitedWindows`, run opportunistically on every
    // `Handle(DesktopLaunchCommand)`, closes that gap without a dedicated
    // timer thread.
    private static readonly TimeSpan MaxWindowIdleAge = TimeSpan.FromHours(12);

    private readonly ApplicationAllowList _allowList;
    private readonly UIA3Automation _automation = new();
    private readonly ConcurrentDictionary<string, LaunchedWindow> _windows = new();
    private readonly Action<string> _log;

    // UI Automation's COM interfaces are not safe for concurrent calls from
    // arbitrary thread-pool threads: `Program.cs` deliberately answers every
    // pushed command on its own background task so a slow operation never
    // blocks reading the next message, which means two `Handle(...)` calls
    // can otherwise run at the same instant against this one shared
    // `_automation` instance. Routing every call through a single dedicated
    // worker thread serializes the actual UIA/COM work without serializing
    // the agent's message loop — callers still get a `Task` they can await
    // concurrently with other in-flight commands.
    private readonly BlockingCollection<Action> _workQueue = new();
    private readonly Thread _worker;

    public DesktopAutomationHandler(ApplicationAllowList allowList, Action<string> log)
    {
        _allowList = allowList;
        _log = log;
        _worker = new Thread(RunWorkerLoop) { IsBackground = true, Name = "KortexDesktopAutomation" };
        _worker.SetApartmentState(ApartmentState.STA);
        _worker.Start();
    }

    private void RunWorkerLoop()
    {
        foreach (var work in _workQueue.GetConsumingEnumerable())
        {
            work();
        }
    }

    /// <summary>
    /// Runs <paramref name="handle"/> on the single dedicated UI Automation
    /// thread and returns its result. Every real command dispatch
    /// (`Program.cs`) goes through this; direct `Handle(...)` calls remain
    /// available and safe for tests that only ever run one command at a
    /// time, but production code must not call `Handle` directly from a
    /// thread-pool task — see the class-level remarks above.
    /// </summary>
    public Task<DesktopCommandResult> ExecuteAsync(Func<DesktopCommandResult> handle)
    {
        var completion = new TaskCompletionSource<DesktopCommandResult>(TaskCreationOptions.RunContinuationsAsynchronously);
        _workQueue.Add(() =>
        {
            try
            {
                completion.SetResult(handle());
            }
            catch (Exception ex)
            {
                completion.SetException(ex);
            }
        });
        return completion.Task;
    }

    public DesktopCommandResult Handle(DesktopLaunchCommand command)
    {
        SweepExitedWindows();

        var allowed = _allowList.Resolve(command.ApplicationId);
        if (allowed is null)
        {
            return Failure(command.CommandId, "APPLICATION_NOT_ALLOWED", "The requested application is not on the allow-list.");
        }

        // Fail closed on arguments: an application with no `AllowedArguments`
        // configured permits a bare launch (zero arguments) only. Treating
        // an absent list as "anything goes" — the inverse of every other
        // allow-list check in this class — would mean an operator who
        // allow-lists an application without remembering to also restrict
        // its arguments has unknowingly granted every `desktop:launch`
        // caller arbitrary command-line control over that executable.
        if (command.Arguments.Count > 0
            && (allowed.AllowedArguments is null || command.Arguments.Any(argument => !allowed.AllowedArguments.Contains(argument))))
        {
            return Failure(command.CommandId, "APPLICATION_NOT_ALLOWED", "One or more arguments are not permitted for this application.");
        }

        // `ProcessStartInfo.ArgumentList`, not a hand-built/joined string:
        // .NET's own argument encoder correctly doubles backslashes before
        // an embedded quote and before the closing quote, which a manual
        // `string.Join(" ", ...Select(EscapeArgument))` got wrong for the
        // (extremely common) case of a Windows path argument ending in a
        // backslash — that bug could silently merge two intended arguments
        // into one at the OS's own parsing boundary.
        var startInfo = new ProcessStartInfo(allowed.Path);
        foreach (var argument in command.Arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        Application application;
        try
        {
            application = Application.Launch(startInfo);
        }
        catch (Exception ex)
        {
            // `ex.Message` stays out of the wire-facing result: a process
            // failing to start commonly embeds the resolved absolute path
            // (`allowed.Path`, this agent's local, operator-managed
            // allow-list target) in its exception text, which the caller —
            // who only ever supplied the opaque `application_id` — must
            // never learn.
            _log($"Launch of '{command.ApplicationId}' failed: {ex.Message}");
            return Failure(command.CommandId, "LAUNCH_FAILED", "The application failed to start.");
        }

        var timeout = command.TimeoutSeconds > 0 ? TimeSpan.FromSeconds(command.TimeoutSeconds) : TimeSpan.FromSeconds(30);
        Window? mainWindow;
        try
        {
            mainWindow = application.GetMainWindow(_automation, timeout);
        }
        catch (Exception ex)
        {
            application.Close();
            _log($"'{command.ApplicationId}' never produced a usable main window: {ex.Message}");
            return Failure(command.CommandId, "LAUNCH_FAILED", "The application never produced a usable main window.");
        }

        if (mainWindow is null)
        {
            application.Close();
            return Failure(command.CommandId, "LAUNCH_FAILED", "The application never produced a usable main window within its timeout.");
        }

        var windowHandle = Guid.NewGuid().ToString("N");
        _windows[windowHandle] = new LaunchedWindow(command.ApplicationId, application, mainWindow, DateTimeOffset.UtcNow);
        _log($"Launched '{command.ApplicationId}' -> window {windowHandle} (pid {application.ProcessId}).");

        return new DesktopCommandResult
        {
            CommandId = command.CommandId,
            Success = true,
            WindowHandle = windowHandle,
            ProcessId = application.ProcessId,
        };
    }

    public DesktopCommandResult Handle(DesktopClickCommand command)
    {
        var resolution = ResolveElement(command.CommandId, command.WindowHandle, command.Selector);
        if (resolution.Error is not null)
        {
            return resolution.Error;
        }

        try
        {
            resolution.Element!.Click();
        }
        catch (Exception ex)
        {
            return Failure(command.CommandId, "ELEMENT_NOT_FOUND", $"The element could not be clicked: {ex.Message}");
        }
        return new DesktopCommandResult { CommandId = command.CommandId, Success = true };
    }

    public DesktopCommandResult Handle(DesktopTypeCommand command)
    {
        var resolution = ResolveElement(command.CommandId, command.WindowHandle, command.Selector);
        if (resolution.Error is not null)
        {
            return resolution.Error;
        }

        try
        {
            var element = resolution.Element!;
            if (element.Patterns.Value.TryGetPattern(out var valuePattern))
            {
                // Sets the control's value directly through UI Automation —
                // deterministic regardless of which window currently has
                // Windows' own keyboard focus. `Keyboard.Type` below sends
                // physical keystrokes to whatever window the OS considers
                // foreground, which is not necessarily the window this
                // command was addressed to (nothing about this agent's
                // design guarantees, or should require, that the target
                // window is the one currently in front).
                valuePattern.SetValue(command.Text);

                // `SetValue` can return before the target application's own
                // UI thread has actually finished applying it — observed
                // directly: a click dispatched immediately afterward could
                // still see the control's *previous* value. Confirming the
                // new value is visible before reporting success turns that
                // race into a bounded wait instead of a caller-visible flake.
                var applied = FlaUI.Core.Tools.Retry.WhileTrue(
                    () => valuePattern.Value.ValueOrDefault != command.Text,
                    timeout: TimeSpan.FromSeconds(2),
                    interval: TimeSpan.FromMilliseconds(20));
                if (!applied.Success)
                {
                    return Failure(command.CommandId, "ELEMENT_NOT_FOUND", "The typed value did not take effect within the expected time.");
                }
            }
            else
            {
                element.Focus();
                Keyboard.Type(command.Text);
            }
        }
        catch (Exception ex)
        {
            // `ex.Message` only — never `command.Text` — reaches the log or
            // the result. The typed value itself has exactly one
            // destination: the target control.
            return Failure(command.CommandId, "ELEMENT_NOT_FOUND", $"Text could not be entered into the element: {ex.Message}");
        }
        return new DesktopCommandResult { CommandId = command.CommandId, Success = true };
    }

    public DesktopCommandResult Handle(DesktopReadTextCommand command)
    {
        var resolution = ResolveElement(command.CommandId, command.WindowHandle, command.Selector);
        if (resolution.Error is not null)
        {
            return resolution.Error;
        }

        string text;
        try
        {
            text = ReadElementText(resolution.Element!);
        }
        catch (Exception ex)
        {
            return Failure(command.CommandId, "ELEMENT_NOT_FOUND", $"The element's text could not be read: {ex.Message}");
        }

        var maxLength = command.MaxLength > 0 ? command.MaxLength : 4096;
        var truncated = text.Length > maxLength;
        return new DesktopCommandResult
        {
            CommandId = command.CommandId,
            Success = true,
            Text = truncated ? text[..maxLength] : text,
            Truncated = truncated,
        };
    }

    private static string ReadElementText(AutomationElement element)
    {
        if (element.Patterns.Value.TryGetPattern(out var valuePattern))
        {
            return valuePattern.Value.ValueOrDefault ?? string.Empty;
        }
        return element.Name ?? string.Empty;
    }

    /// <summary>
    /// Evicts every tracked window whose application has already exited, or
    /// that has sat idle past <see cref="MaxWindowIdleAge"/>, regardless of
    /// whether anything has referenced its `windowHandle` since. Cheap
    /// relative to a launch (a handful of `Process` checks), and keeps this
    /// agent from retaining `Application`/`Window`/process-handle references
    /// indefinitely for windows nobody ever asks about again.
    /// </summary>
    private void SweepExitedWindows()
    {
        var cutoff = DateTimeOffset.UtcNow - MaxWindowIdleAge;
        foreach (var (handle, window) in _windows)
        {
            if (window.Application.HasExited || window.LaunchedAtUtc < cutoff)
            {
                if (_windows.TryRemove(handle, out var removed))
                {
                    CloseApplication(removed.Application);
                }
            }
        }
    }

    private (AutomationElement? Element, DesktopCommandResult? Error) ResolveElement(
        string commandId, string windowHandle, UiElementSelector selector)
    {
        if (!_windows.TryGetValue(windowHandle, out var window))
        {
            return (null, Failure(commandId, "WINDOW_NOT_FOUND", "The window_handle is not known to this agent."));
        }

        if (window.Application.HasExited)
        {
            _windows.TryRemove(windowHandle, out _);
            return (null, Failure(commandId, "WINDOW_NOT_FOUND", "The application for this window_handle has exited."));
        }

        var conditions = new List<ConditionBase>();
        var factory = _automation.ConditionFactory;
        if (!string.IsNullOrEmpty(selector.AutomationId))
        {
            conditions.Add(factory.ByAutomationId(selector.AutomationId));
        }
        if (!string.IsNullOrEmpty(selector.Name))
        {
            conditions.Add(factory.ByName(selector.Name));
        }
        if (!string.IsNullOrEmpty(selector.ControlType) &&
            Enum.TryParse<FlaUI.Core.Definitions.ControlType>(selector.ControlType, ignoreCase: true, out var controlType))
        {
            conditions.Add(factory.ByControlType(controlType));
        }

        if (conditions.Count == 0)
        {
            // Mirrors the backend's own pre-flight rejection
            // (`DesktopInvalidSelectorError`); reached here only if a future
            // caller bypasses that check, so it still fails closed rather
            // than matching every element in the window.
            return (null, Failure(commandId, "ELEMENT_NOT_FOUND", "The selector supplied no usable criteria."));
        }

        var condition = conditions.Count == 1 ? conditions[0] : new AndCondition(conditions.ToArray());

        AutomationElement[] matches;
        try
        {
            matches = window.Window.FindAllDescendants(condition);
        }
        catch (Exception ex)
        {
            return (null, Failure(commandId, "ELEMENT_NOT_FOUND", $"Element search failed: {ex.Message}"));
        }

        return matches.Length switch
        {
            0 => (null, Failure(commandId, "ELEMENT_NOT_FOUND", "No UI element matched the selector.")),
            1 => (matches[0], null),
            _ => (null, Failure(commandId, "ELEMENT_AMBIGUOUS", $"{matches.Length} UI elements matched the selector; a unique match is required.")),
        };
    }

    private static DesktopCommandResult Failure(string commandId, string errorCode, string errorMessage) =>
        new() { CommandId = commandId, Success = false, ErrorCode = errorCode, ErrorMessage = errorMessage };

    private bool _disposed;

    public void Dispose()
    {
        // Idempotent: callers (including test cleanup paths that dispose
        // explicitly and then again via `IDisposable`) may legitimately call
        // this more than once, and `BlockingCollection`/`UIA3Automation`
        // are not safe to dispose or complete twice.
        if (_disposed)
        {
            return;
        }
        _disposed = true;

        // Stop accepting new work and wait for the worker thread to drain
        // before touching `_automation` or any window — otherwise disposal
        // could run concurrently with a UIA call still in flight on that
        // thread.
        _workQueue.CompleteAdding();
        _worker.Join(TimeSpan.FromSeconds(5));

        foreach (var window in _windows.Values)
        {
            CloseApplication(window.Application);
        }
        _windows.Clear();
        _automation.Dispose();
        _workQueue.Dispose();
    }

    /// <summary>
    /// Closes a launched application's process. `Application.Dispose()`
    /// alone releases only this process's automation handle to it — it does
    /// not terminate the target process, which would otherwise linger as an
    /// orphan every time a window this agent launched is torn down.
    /// </summary>
    private static void CloseApplication(Application application)
    {
        try
        {
            if (!application.HasExited)
            {
                application.Close();
            }
        }
        catch
        {
            // Best-effort: a process that is already gone, or that refuses
            // a graceful close, still gets a forceful attempt below.
        }
        finally
        {
            try
            {
                if (!application.HasExited)
                {
                    application.Kill();
                }
            }
            catch
            {
                // Nothing further can be done from here; not fatal to disposal.
            }
            application.Dispose();
        }
    }

    private sealed record LaunchedWindow(string ApplicationId, Application Application, Window Window, DateTimeOffset LaunchedAtUtc);
}
