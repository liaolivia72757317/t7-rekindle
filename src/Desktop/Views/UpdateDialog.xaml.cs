using System;
using System.ComponentModel;
using System.Threading.Tasks;
using System.Windows;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class UpdateDialog : Window
    {
        private readonly UpdateDialogViewModel _model;
        private bool _closingPending;
        private bool _allowClose;

        public UpdateDialog(LauncherUpdateInfo info) : this(info, null) { }

        internal UpdateDialog(LauncherUpdateInfo info, UpdateDialogViewModel model)
        {
            InitializeComponent();
            _model = model ?? new UpdateDialogViewModel(info, LauncherInformation.DownloadInstallerAsync,
                InstallAsync, new DesktopInteraction().OpenAddress);
            DataContext = _model;
            Closing += OnClosing;
            Closed += (_, __) => _model.Dispose();
        }

        private Task<bool> InstallAsync(string path)
        {
            if (!(Owner is MainWindow window)) throw new InvalidOperationException("未找到启动器主窗口。");
            return window.InstallUpdateAsync(path, () => _allowClose = true);
        }

        private async void OnClosing(object sender, CancelEventArgs e)
        {
            if (_allowClose) return;
            if (_model.IsInstalling || _closingPending) { e.Cancel = true; return; }
            if (!_model.IsDownloading) return;
            e.Cancel = true;
            _closingPending = true;
            try
            {
                await _model.CancelAndWaitAsync().ConfigureAwait(true);
                if (_model.HasError) return;
                _allowClose = true;
                await Dispatcher.InvokeAsync(new Action(Close));
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "结束下载失败：" + error.Message, "启动器更新", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
            finally { _closingPending = false; }
        }
    }
}
