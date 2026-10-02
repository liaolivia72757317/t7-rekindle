using System.Windows;
using System.Windows.Controls;

namespace T7.Rekindle.Desktop.Views
{
    public partial class LaunchPage : UserControl
    {
        public LaunchPage() { InitializeComponent(); }

        private void OnNameRowSizeChanged(object sender, SizeChangedEventArgs e)
        {
            var compact = e.NewSize.Width < 800;
            Grid.SetRow(NameHint, compact ? 1 : 0);
            Grid.SetColumn(NameHint, compact ? 0 : 1);
            Grid.SetColumnSpan(NameHint, compact ? 2 : 1);
            NameHint.Margin = compact ? new Thickness(0, 8, 0, 0) : new Thickness(20, 0, 0, 0);
        }
    }
}
