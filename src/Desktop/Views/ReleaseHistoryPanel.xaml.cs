using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class ReleaseHistoryPanel : UserControl
    {
        public ReleaseHistoryPanel() { InitializeComponent(); }

        private void OnHistoryChanged(object sender, DependencyPropertyChangedEventArgs args)
        {
            if (args.OldValue is ReleaseHistoryViewModel previous)
                PropertyChangedEventManager.RemoveHandler(previous, OnSummaryChanged, nameof(ReleaseHistoryViewModel.Summary));
            if (args.NewValue is ReleaseHistoryViewModel current)
                PropertyChangedEventManager.AddHandler(current, OnSummaryChanged, nameof(ReleaseHistoryViewModel.Summary));
            ((FlowDocumentScrollViewer)sender).Document = MarkdownDocument.Render((args.NewValue as ReleaseHistoryViewModel)?.Summary);
        }

        private void OnSummaryChanged(object sender, PropertyChangedEventArgs args) =>
            HistoryNotes.Document = MarkdownDocument.Render(((ReleaseHistoryViewModel)sender).Summary);
    }
}
