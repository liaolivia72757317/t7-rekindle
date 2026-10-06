using System.Windows;
using System.Windows.Controls;

namespace T7.Rekindle.Desktop.Views
{
    public partial class AboutPage : UserControl
    {
        private bool? _compact;

        public AboutPage() { InitializeComponent(); }

        private void OnContentSizeChanged(object sender, SizeChangedEventArgs e)
        {
            if (e.NewSize.Width <= 0) return;
            var compact = e.NewSize.Width < 720;
            if (_compact == compact) return;
            _compact = compact;

            ClientSupportGrid.ColumnDefinitions[1].Width = compact ? new GridLength(0) : new GridLength(2, GridUnitType.Star);
            Grid.SetRowSpan(ClientDownloadButton, compact ? 1 : 2);
            ClientDownloadButton.Margin = compact ? new Thickness(0) : new Thickness(0, 0, 8, 0);
            Grid.SetColumn(EnvironmentInfoButton, compact ? 0 : 1);
            Grid.SetRow(EnvironmentInfoButton, compact ? 1 : 0);
            EnvironmentInfoButton.Margin = compact ? new Thickness(0, 12, 0, 0) : new Thickness(8, 0, 0, 8);
            Grid.SetColumn(IssuesButton, compact ? 0 : 1);
            Grid.SetRow(IssuesButton, compact ? 2 : 1);
            IssuesButton.Margin = compact ? new Thickness(0, 12, 0, 0) : new Thickness(8, 8, 0, 0);

            ProjectLinksGrid.Columns = compact ? 1 : 3;
            RepositoryButton.Margin = compact ? new Thickness(0, 0, 0, 12) : new Thickness(0, 0, 8, 0);
            LicensesButton.Margin = compact ? new Thickness(0, 0, 0, 12) : new Thickness(4, 0, 4, 0);
            ThanksButton.Margin = compact ? new Thickness(0, 0, 0, 12) : new Thickness(8, 0, 0, 0);
        }
    }
}
