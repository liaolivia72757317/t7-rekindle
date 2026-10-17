using System;
using System.ComponentModel;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        public string AnnouncementTitle => _announcements.Current.Title;
        public string AnnouncementSummary => _announcements.Current.Summary;
        internal Task AnnouncementTask => _announcements.Loading;
        internal void StartAnnouncementChecks() => _announcements.Start();
        internal Task RefreshAnnouncementAsync() => _announcements.RefreshAsync();
        private void OnAnnouncementChanged(object sender, PropertyChangedEventArgs args)
        {
            OnPropertyChanged(nameof(AnnouncementTitle));
            OnPropertyChanged(nameof(AnnouncementSummary));
        }
        private void OnAnnouncementFailed(Exception error) =>
            _log.Write(DiagnosticLogLevel.Warn, "获取首页公告失败，使用内置公告", error);
        private void ShowAnnouncement()
        {
            var announcement = _announcements.Current;
            try { _interaction.ShowMarkdown(announcement.Title, announcement.BodyMarkdown); }
            catch (Exception error) { ReportUiError("打开公告失败", error); }
        }
    }
}
