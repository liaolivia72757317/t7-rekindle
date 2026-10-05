using System;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.ManagedHarness
{
    internal static class LauncherToastTests
    {
        internal static void Run(string directory, string output)
        {
            var now = DateTime.UtcNow;
            var center = new NoticeCenter();
            center.PublishAt("copy", "诊断信息已复制", NoticeSeverity.Success, now);
            center.PublishAt("copy", "诊断信息已复制", NoticeSeverity.Success, now.AddMilliseconds(100));
            LauncherTests.Assert(center.Visible.Count == 1 && center.History.Count == 1, "notices do not deduplicate");
            var copy = center.Visible[0];
            copy.IsPaused = true;
            center.Tick(now.AddSeconds(10));
            LauncherTests.Assert(center.Visible.Count == 1, "hover/focus did not pause the notice");
            copy.IsPaused = false;
            center.IsActive = false;
            center.Tick(now.AddSeconds(20));
            center.IsActive = true;
            center.Tick(now.AddSeconds(22));
            LauncherTests.Assert(center.Visible.Count == 1, "inactive time consumed notice duration");
            center.Tick(now.AddSeconds(24));
            LauncherTests.Assert(center.Visible.Count == 0 && center.History.Count == 1, "success did not expire into history");
            center.PublishAt("error", "设置未保存，请重试", NoticeSeverity.Error, now.AddSeconds(25));
            center.Tick(now.AddDays(1));
            LauncherTests.Assert(center.Visible.Count == 1, "actionable error expired");
            for (var i = 0; i < 30; i++) center.PublishAt("success-" + i, "复制完成", NoticeSeverity.Success, now.AddDays(1));
            LauncherTests.Assert(center.Visible.Count == 2 && center.WaitingCount == 5 && center.History.Count == 20
                && center.Visible[0].Severity == NoticeSeverity.Error, "success overflow displaced an error or escaped queue bounds");
            center.Visible[0].CloseCommand.Execute(null);
            LauncherTests.Assert(center.Visible.Count == 2 && center.WaitingCount == 4, "close did not advance the queue");
        }
    }
}
