using System;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class LauncherUpdatePage : UserControl
    {
        public LauncherUpdatePage() { InitializeComponent(); }

        private void OnBodySizeChanged(object sender, SizeChangedEventArgs e)
        {
            UpdateBody.Height = Math.Max(440, e.NewSize.Height);
        }

        private void OnHistorySizeChanged(object sender, SizeChangedEventArgs e) =>
            HistoryContent.Height = Math.Max(320, e.NewSize.Height);

        private void OnChangelogDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (e.OldValue is AboutViewModel previous)
                PropertyChangedEventManager.RemoveHandler(previous, OnUpdateSummaryChanged, nameof(AboutViewModel.UpdateSummary));
            if (e.NewValue is AboutViewModel current)
                PropertyChangedEventManager.AddHandler(current, OnUpdateSummaryChanged, nameof(AboutViewModel.UpdateSummary));
            ((FlowDocumentScrollViewer)sender).Document = MarkdownDocument.Render((e.NewValue as AboutViewModel)?.UpdateSummary);
        }

        private void OnUpdateSummaryChanged(object sender, PropertyChangedEventArgs e) =>
            ChangelogViewer.Document = MarkdownDocument.Render(((AboutViewModel)sender).UpdateSummary);
    }
}
