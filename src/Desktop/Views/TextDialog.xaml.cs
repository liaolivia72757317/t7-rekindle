using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Threading;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class TextDialog : Window
    {
        public TextDialog(string title, string text) : this(title, text, false) { }

        private readonly WindowLayoutController _layout;
        internal Action<string> CopyDiagnosticText { get; set; } = value => Clipboard.SetText(value);

        internal TextDialog(MainWindowViewModel model) : this("启动诊断", string.Empty, false, true)
        {
            DocumentText.SetBinding(TextBox.TextProperty, new MultiBinding
            {
                Mode = BindingMode.OneWay,
                StringFormat = "当前状态：{0}\n\n{1}\n\n{2}\n\n{3}",
                Bindings =
                {
                    new Binding(nameof(model.StatusText)) { Source = model },
                    new Binding(nameof(model.DiagnosticText)) { Source = model },
                    new Binding(nameof(model.EndpointText)) { Source = model },
                    new Binding(nameof(model.NativeLogText)) { Source = model }
                }
            });
        }

        public TextDialog(string title, string text, bool renderMarkdown, bool showDiagnostics = false)
        {
            InitializeComponent();
            Title = title;
            DiagnosticActions.Visibility = showDiagnostics ? Visibility.Visible : Visibility.Collapsed;
            LocalFeedback.Visibility = Visibility.Collapsed;
            if (showDiagnostics && !renderMarkdown)
            {
                DocumentText.TextWrapping = TextWrapping.NoWrap;
                DocumentText.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto;
                DocumentText.Loaded += (_, __) => ScrollLogsToBottom();
                DocumentText.TextChanged += (_, __) => ScrollLogsToBottom();
            }
            if (renderMarkdown)
            {
                DocumentText.Visibility = Visibility.Collapsed;
                MarkdownHost.Visibility = Visibility.Visible;
                MarkdownViewer.Document = MarkdownDocument.Render(text);
            }
            else DocumentText.Text = text;
            if (!showDiagnostics && !renderMarkdown && (string.IsNullOrWhiteSpace(text) || (Uri.TryCreate(text, UriKind.Absolute, out var address)
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

        private void ScrollLogsToBottom() => Dispatcher.BeginInvoke(DispatcherPriority.Loaded,
            new Action(() => DocumentText.ScrollToVerticalOffset(double.PositiveInfinity)));

        private void OnCopyDiagnostics(object sender, RoutedEventArgs e)
        {
            try { CopyDiagnosticText(DiagnosticSanitizer.Redact(DocumentText.Text)); Feedback("诊断信息已复制", false); }
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
