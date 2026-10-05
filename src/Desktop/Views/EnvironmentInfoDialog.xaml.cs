using System.Windows;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class EnvironmentInfoDialog : Window
    {
        private readonly EnvironmentInfoViewModel _model;
        private readonly WindowLayoutController _layout;

        public EnvironmentInfoDialog() : this(new EnvironmentInfoViewModel(RuntimeEnvironmentInformation.CollectAsync, Clipboard.SetText)) { }

        internal EnvironmentInfoDialog(EnvironmentInfoViewModel model)
        {
            InitializeComponent();
            _model = model;
            DataContext = model;
            _layout = new WindowLayoutController(this, error => new LogService().Error("调整环境信息窗口失败", error), Width, Height);
            Loaded += OnLoaded;
            Closed += (_, __) => { _model.Dispose(); _layout.Dispose(); };
        }

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            Loaded -= OnLoaded;
            await _model.RefreshCommand.ExecuteAsync(null);
        }
    }
}
