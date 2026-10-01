using System.Windows;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.Views
{
    public partial class UpdateDialog : Window
    {
        private readonly LauncherUpdateInfo _info;
        public UpdateDialog(LauncherUpdateInfo info)
        {
            InitializeComponent();
            _info = info;
            DataContext = info;
            if (!info.IsNewVersion) Heading.Text = "已是最新版本";
        }

        private void OnDownload(object sender, RoutedEventArgs e)
        {
            if (!_info.HasDownloadAddress) return;
            new TextDialog("下载地址", _info.DownloadAddress)
                { Owner = this }.ShowDialog();
        }
    }
}
