using System.Windows;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.Views
{
    public partial class ConfirmationDialog : Window
    {
        private readonly WindowLayoutController _layout;
        public ConfirmationDialog(string title, string message, string action)
        {
            InitializeComponent();
            Title = Heading.Text = title;
            Message.Text = message;
            ConfirmButton.Content = action;
            _layout = new WindowLayoutController(this, error => new LogService().Error("调整确认窗口失败", error), Width, Height);
            Closed += (_, __) => _layout.Dispose();
        }
        private void OnConfirm(object sender, RoutedEventArgs e) { DialogResult = true; }
    }
}
