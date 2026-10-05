using System;
using System.Windows;
using System.Windows.Media;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class TextDialog : Window
    {
        public TextDialog(string title, string text) : this(title, text, false) { }

        private readonly string _diagnostics;
        private readonly WindowLayoutController _layout;
        internal Action<string> CopyDiagnosticText { get; set; } = value => Clipboard.SetText(value);

        public TextDialog(string title, string text, bool renderMarkdown, bool showDiagnostics = false)
        {
            InitializeComponent();
            Title = title;
            _diagnostics = showDiagnostics ? DiagnosticSanitizer.Redact(text) : text;
            DiagnosticActions.Visibility = showDiagnostics ? Visibility.Visible : Visibility.Collapsed;
            LocalFeedback.Visibility = Visibility.Collapsed;
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
            _layout = new WindowLayoutController(this, error =>
            {
                new LogService().Error("调整说明窗口失败", error);
                Feedback("窗口适配失败，请关闭后重试", true);
            }, Width, Height);
            Closed += (_, __) => _layout.Dispose();
        }

        private void OnCopyDiagnostics(object sender, RoutedEventArgs e)
        {
            try { CopyDiagnosticText(_diagnostics); Feedback("诊断信息已复制", false); }
            catch (Exception error)
            {
                new LogService().Error("复制诊断失败", error);
                Feedback("复制失败，请重试", true);
            }
        }

        private void OnOpenLogs(object sender, RoutedEventArgs e)
        {
            try { new DesktopInteraction().OpenDirectory(MainWindowViewModel.LogDirectory); }
            catch (Exception error)
            {
                new LogService().Error("打开日志目录失败", error);
                Feedback("日志目录暂不可用，请检查目录访问权限", true);
            }
        }

        private void Feedback(string text, bool error)
        {
            LocalFeedback.Text = text;
            LocalFeedback.Foreground = (Brush)FindResource(error ? "DangerBrush" : "SuccessBrush");
            LocalFeedback.Visibility = Visibility.Visible;
        }
    }
}
