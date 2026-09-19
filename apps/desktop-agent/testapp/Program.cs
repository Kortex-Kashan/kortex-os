// KORTEX Desktop Automation E2E fixture (Phase 7). See the .csproj header
// for why this exists instead of automating Notepad or Calculator.
//
// The scenario it exists to prove is literally "7 x 8 = 56": two input
// boxes, a Multiply button, and a result label, each with a frozen
// AutomationId, so a real FlaUI-driven agent can type two numbers, click a
// real button, and read back a real, freshly computed result — proving the
// full type -> click -> read round trip against a live window, not just
// that a value can be read back unchanged.

using System.Windows.Forms;

namespace Kortex.Agent.TestApp;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        Application.SetHighDpiMode(HighDpiMode.SystemAware);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new MainForm());
    }
}

public sealed class MainForm : Form
{
    public MainForm()
    {
        Text = "KORTEX Automation Test App";
        Width = 360;
        Height = 220;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;

        // Deliberately no `AccessibleName` override on any control: for a
        // Label in particular, an explicit AccessibleName pins the UIA
        // `Name` property to that fixed string, which would hide the live
        // `Text` this fixture exists to prove is genuinely recomputed —
        // `Name` (the AutomationId source) is enough for deterministic
        // targeting on its own.
        var inputA = new TextBox { Left = 20, Top = 20, Width = 100, Name = "InputA" };
        var inputB = new TextBox { Left = 140, Top = 20, Width = 100, Name = "InputB" };
        var multiply = new Button
        {
            Left = 20,
            Top = 60,
            Width = 220,
            Text = "Multiply",
            Name = "MultiplyButton",
        };
        var result = new Label
        {
            Left = 20,
            Top = 110,
            Width = 220,
            Height = 30,
            Text = string.Empty,
            Name = "ResultLabel",
            BorderStyle = BorderStyle.Fixed3D,
        };

        multiply.Click += (_, _) =>
        {
            result.Text =
                int.TryParse(inputA.Text, out var a) && int.TryParse(inputB.Text, out var b)
                    ? (a * b).ToString()
                    : "ERROR";
        };

        Controls.Add(inputA);
        Controls.Add(inputB);
        Controls.Add(multiply);
        Controls.Add(result);
    }
}
