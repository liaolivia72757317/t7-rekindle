using System;
using System.Windows;

namespace T7.Rekindle.Desktop.Views
{
    public partial class TextDialog : Window
    {
        public TextDialog(string title, string text) : this(title, text, false) { }

        public TextDialog(string title, string text, bool renderMarkdown)
        {
            InitializeComponent();
            Title = title;
            if (renderMarkdown)
            {
                DocumentText.Visibility = Visibility.Collapsed;
                MarkdownHost.Visibility = Visibility.Visible;
                MarkdownViewer.Document = MarkdownDocument.Render(text);
            }
            else DocumentText.Text = text;
            if (!renderMarkdown && (string.IsNullOrWhiteSpace(text) || (Uri.TryCreate(text, UriKind.Absolute, out var address)
                && (address.Scheme == Uri.UriSchemeHttps || address.Scheme == Uri.UriSchemeHttp))))
            {
                Width = 560;
                Height = 260;
                MinHeight = 220;
            }
        }
    }
}
