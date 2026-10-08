using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;

namespace T7.ManagedHarness
{
    internal static class EnvironmentInformationTests
    {
        internal static void Run(string outputDirectory)
        {
            TestReport();
            TestCommands();
            TestDialog(outputDirectory);
            TestAutomaticDetection();
        }

        private static void TestReport()
        {
            Assert(RuntimeEnvironmentInformation.DescribeFrameworkRelease(null).Contains("未检出"), "missing Framework release was accepted");
            Assert(RuntimeEnvironmentInformation.DescribeFrameworkRelease(528039).Contains("低于 4.8"), "old Framework was accepted");
            Assert(RuntimeEnvironmentInformation.DescribeFrameworkRelease(528040) == "4.8 · Release 528040", "Framework 4.8 lower bound was rejected");
            Assert(RuntimeEnvironmentInformation.DescribeFrameworkRelease(533319) == "4.8 · Release 533319", "Framework 4.8 was misidentified");
            Assert(RuntimeEnvironmentInformation.DescribeFrameworkRelease(533320) == "≥4.8.1 · Release 533320", "Framework 4.8.1 lower bound was rejected");
            Assert(RuntimeEnvironmentInformation.DescribeFrameworkRelease(600000) == "≥4.8.1 · Release 600000", "future Framework release was rejected");
            Assert(RuntimeEnvironmentInformation.FormatMemory(16UL * 1024 * 1024 * 1024) == "16.0 GiB", "physical memory uses incorrect units");
            Assert(RuntimeEnvironmentInformation.FormatMemory(0) == "0.0 GiB", "zero available memory was lost");
            Assert(Direct3DEnvironment.DescribeCapabilities(0xfffe0300, 0xffff0300)
                == "HAL x64 · PS/VS 3.0 达标 · 此处检测系统默认显卡", "shader summary does not match the reference format");
            Assert(Direct3DEnvironment.DescribeCapabilities(0xfffe0200, 0xffff0300).Contains("未达标"), "old vertex shader was accepted");
            Assert(Direct3DEnvironment.DescribeCapabilities(0xfffe0300, 0xffff0200).Contains("未达标"), "old pixel shader was accepted");
            Assert(Direct3DEnvironment.DescribeCapabilities(0, 0).Contains("未达标"), "missing shader capability was accepted");
            Assert(Direct3DEnvironment.FormatDriverVersion(0x001f0000000f1234) == "31.0.15.4660", "driver version words were reversed");
            Assert(Direct3DEnvironment.DescribeAdapter(0, "Test GPU", 0x001f0000000f1234)
                == "Test GPU（#1，默认）· 驱动 31.0.15.4660", "default adapter format is incorrect");
            Assert(Direct3DEnvironment.DescribeAdapter(1, "Second GPU", 0x001f0000000f1234)
                == "Second GPU（#2）· 驱动 31.0.15.4660", "secondary adapter was marked as default");
            Assert(RuntimeEnvironmentInformation.FormatCpuName("13th Gen Test(R) Core(TM)   Processor") == "Test Core Processor",
                "CPU display name retained redundant generation, trademark or whitespace text");
            Assert(RuntimeEnvironmentInformation.FormatCpuName("Test 16-Core Processor") == "Test 16-Core Processor", "CPU model information was removed");
            Assert(Marshal.SizeOf(typeof(Direct3DEnvironment.AdapterIdentifier)) == 1104, "D3DADAPTER_IDENTIFIER9 ABI size changed");
            Assert(Marshal.OffsetOf(typeof(Direct3DEnvironment.AdapterIdentifier), "DriverVersion").ToInt32() == 1056,
                "D3DADAPTER_IDENTIFIER9 driver alignment changed");
            Assert(Marshal.SizeOf(typeof(Direct3DEnvironment.DeviceCapabilities)) == 304, "D3DCAPS9 ABI size changed");
            Assert(Marshal.OffsetOf(typeof(Direct3DEnvironment.DeviceCapabilities), "PixelShaderVersion").ToInt32() == 204,
                "D3DCAPS9 pixel shader offset changed");

            var report = new StringBuilder();
            RuntimeEnvironmentInformation.AppendSection(report, "CPU", () => { throw new IOException(@"C:\Users\PRIVATE\fixture"); });
            RuntimeEnvironmentInformation.AppendSection(report, "内存", () => "16.0 GiB");
            RuntimeEnvironmentInformation.AppendSection(report, "显卡", () => " ");
            Assert(report.ToString().Contains("检测失败") && report.ToString().Contains("16.0 GiB")
                && report.ToString().Contains("未检出") && !report.ToString().Contains("PRIVATE"),
                "one probe failure lost other results or exposed an exception path");
            Assert(report.ToString().Contains("内存：16.0 GiB\r\n") && report.ToString().Contains("显卡：未检出\r\n")
                && !report.ToString().Contains("\r\n\r\n"), "single-value sections contain redundant headings or blank lines");
            var compact = new StringBuilder();
            RuntimeEnvironmentInformation.AppendSection(compact, "显卡", () => "\r\nTest GPU\r\n  \r\nSecond GPU\n");
            Assert(compact.ToString() == "显卡：Test GPU；Second GPU\r\n", "an item was not condensed into one report line");
            Assert(RuntimeEnvironmentInformation.DescribeRuntimeFile(Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"), "d3dx9_43.dll"))
                == "未检出", "missing runtime file was accepted");
            Assert(Version.TryParse(RuntimeEnvironmentInformation.DescribeRuntimeFile(typeof(EnvironmentInformationTests).Assembly.Location), out var fileVersion)
                && fileVersion.Revision >= 0, "existing versioned binary did not report a compact four-part version");
            TestRuntimeSummaries();
        }

        private static void TestRuntimeSummaries()
        {
            Assert(RuntimeEnvironmentInformation.CompactFileVersion("11.00.51106.1") == "11.00.51106.1", "version number padding was lost");
            Assert(RuntimeEnvironmentInformation.CompactFileVersion("10.0.26100.1 (WinBuild.160101.0800)") == "10.0.26100.1",
                "version retained build annotations");
            Assert(RuntimeEnvironmentInformation.CompactFileVersion(null) == "", "missing version was fabricated");
            var files = new[] { "msvcr110.dll", "msvcp110.dll" };
            Assert(RuntimeEnvironmentInformation.DescribeRuntimeFiles(files, _ => "11.00.51106.1")
                == "msvcr110.dll / msvcp110.dll 均为 11.00.51106.1", "identical runtime versions were not combined");
            Assert(RuntimeEnvironmentInformation.DescribeRuntimeFiles(files, name => name == files[0] ? "11.00.51106.1" : "11.0.61030.0")
                == "msvcr110.dll 11.00.51106.1；msvcp110.dll 11.0.61030.0", "different runtime versions were merged");
            Assert(RuntimeEnvironmentInformation.DescribeRuntimeFiles(new[] { "d3d9.dll", "d3dx9_43.dll" },
                name => name == "d3d9.dll" ? "10.0.26100.1" : "未检出") == "d3d9.dll 10.0.26100.1；d3dx9_43.dll 未检出",
                "DX9 version or missing component information was lost");
            Assert(RuntimeEnvironmentInformation.DescribeRuntimeFiles(files, _ => "未检出") == "msvcr110.dll 未检出；msvcp110.dll 未检出",
                "missing components were presented as matching versions");
            var partial = RuntimeEnvironmentInformation.DescribeRuntimeFiles(files, name =>
            {
                if (name == files[0]) throw new IOException(@"C:\Users\PRIVATE\fixture");
                return "11.00.51106.1";
            });
            Assert(partial.Contains("msvcr110.dll 检测失败") && partial.Contains("msvcp110.dll 11.00.51106.1") && !partial.Contains("PRIVATE"),
                "a runtime probe failure hid another component or exposed a private path");
        }

        private static void TestCommands()
        {
            var interaction = new FakeDesktopInteraction();
            var about = new AboutViewModel(interaction);
            about.ShowEnvironmentInfoCommand.Execute(null);
            Assert(interaction.EnvironmentInfoCount == 1, "about entry did not open environment information");

            var pending = new TaskCompletionSource<string>();
            var calls = 0;
            var copied = "";
            var clipboardFails = false;
            using (var model = new EnvironmentInfoViewModel(() => { calls++; return pending.Task; }, text =>
            {
                if (clipboardFails) throw new ExternalException("clipboard busy");
                copied = text;
            }))
            {
                Assert(!model.CopyCommand.CanExecute(null), "copy is enabled before detection");
                var loading = model.RefreshCommand.ExecuteAsync(null);
                Assert(model.IsLoading && !model.RefreshCommand.CanExecute(null) && !model.CopyCommand.CanExecute(null),
                    "detection did not disable duplicate requests and copy");
                LauncherTests.RunTask(model.RefreshCommand.ExecuteAsync(null));
                Assert(calls == 1 && !loading.IsCompleted, "duplicate detection was started");
                pending.SetResult("CPU\r\nTest processor\r\n内存\r\n16.0 GiB");
                LauncherTests.RunTask(loading);
                Assert(model.Report.Contains("Test processor") && !model.IsLoading && model.CopyCommand.CanExecute(null), "report did not load");
                model.CopyCommand.Execute(null);
                Assert(copied == model.Report && model.StatusText.Contains("已复制"), "copy differs from the displayed report");
                clipboardFails = true;
                model.CopyCommand.Execute(null);
                Assert(model.HasError && model.StatusText.Contains("复制失败"), "clipboard failure was swallowed");
                clipboardFails = false;
                model.CopyCommand.Execute(null);
                Assert(!model.HasError, "successful copy retained an old error");

                pending = new TaskCompletionSource<string>();
                var failed = model.RefreshCommand.ExecuteAsync(null);
                Assert(model.Report.Length == 0 && !model.CopyCommand.CanExecute(null), "refresh retained a stale report");
                pending.SetException(new IOException("probe fixture"));
                LauncherTests.RunTask(failed);
                Assert(model.HasError && !model.IsLoading && model.RefreshCommand.CanExecute(null) && !model.CopyCommand.CanExecute(null),
                    "failed detection has no retry or permits copying an incomplete report");
                pending = new TaskCompletionSource<string>();
                var retry = model.RefreshCommand.ExecuteAsync(null);
                pending.SetResult("fresh report");
                LauncherTests.RunTask(retry);
                Assert(!model.HasError && model.Report == "fresh report", "detection retry did not recover");
            }
            var delayed = new TaskCompletionSource<string>();
            var closed = new EnvironmentInfoViewModel(() => delayed.Task, _ => { });
            var operation = closed.RefreshCommand.ExecuteAsync(null);
            closed.Dispose();
            delayed.SetResult("late report");
            LauncherTests.RunTask(operation);
            Assert(closed.Report.Length == 0 && !closed.CopyCommand.CanExecute(null) && !closed.RefreshCommand.CanExecute(null),
                "closed dialog accepted a late result");
        }

        private static void TestDialog(string outputDirectory)
        {
            var pending = new TaskCompletionSource<string>();
            var copied = "";
            var model = new EnvironmentInfoViewModel(() => pending.Task, value => copied = value);
            var dialog = new EnvironmentInfoDialog(model);
            var root = (FrameworkElement)dialog.Content;
            var loading = model.RefreshCommand.ExecuteAsync(null);
            LauncherLayoutTests.Render(root, null, outputDirectory, "environment-loading", 684, 581);
            var text = new StringBuilder("T7-Rekindle 环境信息 · 2026-10-05 22:40:27 +08:00\r\n启动器：v0.1.0 · 进程 x64 · 提交未记录\r\n");
            RuntimeEnvironmentInformation.AppendSection(text, "系统", () => "Windows 11 Pro 25H2 · 64 位 · Build 26200.8875");
            RuntimeEnvironmentInformation.AppendSection(text, "CPU", () => "Test processor · 16 逻辑处理器");
            RuntimeEnvironmentInformation.AppendSection(text, "内存", () => "32.0 GiB · 可用 16.0 GiB · 使用率 50%");
            RuntimeEnvironmentInformation.AppendSection(text, "显卡", () => Direct3DEnvironment.DescribeAdapter(0, "Test graphics adapter", 0x001f0000000f1234));
            RuntimeEnvironmentInformation.AppendSection(text, "D3D9", () => Direct3DEnvironment.DescribeCapabilities(0xfffe0300, 0xffff0300));
            RuntimeEnvironmentInformation.AppendSection(text, ".NET Framework", () => RuntimeEnvironmentInformation.DescribeFrameworkRelease(533320) + " · CLR 4.0.30319.42000");
            RuntimeEnvironmentInformation.AppendSection(text, "DX9（系统 x86）", () => "d3d9.dll 10.0.26100.1；d3dx9_43.dll 未检出");
            RuntimeEnvironmentInformation.AppendSection(text, "VC++ 2012（系统 x86）", () => RuntimeEnvironmentInformation.DescribeRuntimeFiles(
                new[] { "msvcr110.dll", "msvcp110.dll" }, _ => "11.00.51106.1"));
            pending.SetResult(text.ToString());
            LauncherTests.RunTask(loading);
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
            {
                LauncherLayoutTests.Render(root, null, outputDirectory, "environment-" + (int)(scale * 100), 684, 581, scale);
                VerifyButtons(dialog, root, 684, 581);
                LauncherLayoutTests.Render(root, null, outputDirectory, "environment-compact-" + (int)(scale * 100), 464, 281, scale);
                VerifyButtons(dialog, root, 464, 281);
            }
            var report = (TextBox)dialog.FindName("ReportText");
            Assert(report.IsReadOnly && report.Text == text.ToString() && report.ExtentHeight > report.ViewportHeight,
                "report is not selectable, read-only and scrollable");
            ((Button)dialog.FindName("CopyButton")).Command.Execute(null);
            Assert(copied == report.Text, "dialog copy did not include the entire report");
            dialog.Close();
        }

        private static void VerifyButtons(Window dialog, FrameworkElement root, double width, double height)
        {
            foreach (var name in new[] { "CopyButton", "RefreshButton", "CloseButton" })
            {
                var button = (Button)dialog.FindName(name);
                var position = button.TranslatePoint(new Point(), root);
                Assert(button.ActualHeight > 0 && position.X >= 0 && position.Y >= 0
                    && position.X + button.ActualWidth <= width + 1 && position.Y + button.ActualHeight <= height + 1,
                    "environment dialog action escaped the viewport: " + name);
            }
        }

        private static void TestAutomaticDetection()
        {
            var calls = 0;
            using (var model = new EnvironmentInfoViewModel(() => { calls++; return Task.FromResult("loaded report"); }, _ => { }))
            {
                var dialog = new EnvironmentInfoDialog(model) { Opacity = 0, ShowActivated = false };
                try
                {
                    dialog.Show();
                    LauncherTests.Pump();
                    Assert(calls == 1 && model.Report == "loaded report", "opening the dialog did not automatically collect information");
                }
                finally { dialog.Close(); }
                Assert(!model.RefreshCommand.CanExecute(null), "closing the dialog did not release its model");
            }
        }

        private static void Assert(bool condition, string message) => LauncherTests.Assert(condition, message);
    }
}
