using System.Windows;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.Views
{
    public partial class RecentNoticesDialog : Window
    {
        private readonly WindowLayoutController _layout;
        public RecentNoticesDialog(NoticeCenter notices)
        {
            InitializeComponent();
            DataContext = notices;
            EmptyState.Visibility = notices.History.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            _layout = new WindowLayoutController(this, error => new LogService().Error("调整近期提示窗口失败", error), Width, Height);
            Closed += (_, __) => _layout.Dispose();
        }
        private void OnNoticeAction(object sender, RoutedEventArgs e)
        {
            var notice = (Notice)((FrameworkElement)sender).DataContext;
            Close();
            notice.ActionCommand.Execute(null);
        }
    }
}
