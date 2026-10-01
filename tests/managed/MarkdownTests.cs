using System;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class MarkdownTests
    {
        internal static void Run()
        {
            var changelog = MarkdownDocument.Render(LauncherInformation.ReadDocument("CHANGELOG.md"));
            var blocks = changelog.Blocks.ToArray();
            Assert(blocks.Length == 3 && blocks[0] is Paragraph && blocks[1] is Paragraph && blocks[2] is List,
                "changelog headings and bullets were not rendered as document blocks");
            Assert(Text(blocks[0]).Trim() == "启动器版本日志" && Text(blocks[1]).Trim() == "0.1.0"
                && blocks[0].FontSize > blocks[1].FontSize && blocks[1].FontSize > changelog.FontSize,
                "Markdown heading markers or typography are incorrect");
            Assert(((List)blocks[2]).ListItems.Count == 7 && !Text(changelog).Contains("`"),
                "changelog bullets or inline code still contain Markdown syntax");
            var thanks = MarkdownDocument.Render(LauncherInformation.ReadDocument("THANKS.md"));
            Assert(thanks.Blocks.Count == 2 && Text(thanks.Blocks.FirstBlock).Trim() == "特别感谢"
                && Text(thanks).Contains("社区支持的贡献者"), "thanks document lost its heading or paragraph");

            var formatting = MarkdownDocument.Render("# Heading\r\n\r\n**strong** and *emphasis* and `**literal**` and \\*plain\\*\r\ncontinued\r\n\r\n3. Third\r\n4. Fourth\r\n\r\n## Next\r\n\r\n+ Item");
            blocks = formatting.Blocks.ToArray();
            var paragraph = (Paragraph)blocks[1];
            Assert(paragraph.Inlines.OfType<Bold>().Count() == 1 && paragraph.Inlines.OfType<Italic>().Count() == 1,
                "inline emphasis was not rendered");
            Assert(Text(paragraph).Contains("**literal**") && Text(paragraph).Contains("*plain* continued"),
                "inline code, escaped punctuation, or continued paragraphs were corrupted");
            var code = paragraph.Inlines.OfType<Run>().Single(run => run.Text == "**literal**");
            Assert(Equals(code.FontFamily, Application.Current.Resources["CodeFontFamily"]), "inline code lost its code font");
            var ordered = (List)blocks[2];
            Assert(ordered.MarkerStyle == TextMarkerStyle.Decimal && ordered.StartIndex == 3 && ordered.ListItems.Count == 2,
                "ordered list numbering was not preserved");
            Assert(blocks[3] is Paragraph && blocks[4] is List, "a heading did not end the previous list");
            var literal = MarkdownDocument.Render("#not-a-heading\n\n`unfinished and *unclosed\n\n<script>literal text</script>");
            Assert(Text(literal).Contains("#not-a-heading") && Text(literal).Contains("`unfinished and *unclosed")
                && Text(literal).Contains("<script>literal text</script>"), "literal content was lost or interpreted as markup");
            Assert(MarkdownDocument.Render(null).Blocks.Count == 0, "empty Markdown created placeholder content");

            var dialog = new TextDialog("版本日志", "# Heading", true);
            Assert(((TextBox)dialog.FindName("DocumentText")).Visibility == Visibility.Collapsed
                && ((Border)dialog.FindName("MarkdownHost")).Visibility == Visibility.Visible
                && Text(((FlowDocumentScrollViewer)dialog.FindName("MarkdownViewer")).Document).Trim() == "Heading",
                "Markdown dialog did not switch to the rendered document");
            var plain = new TextDialog("开源软件说明", "# literal license text");
            Assert(((TextBox)plain.FindName("DocumentText")).Text == "# literal license text"
                && ((Border)plain.FindName("MarkdownHost")).Visibility == Visibility.Collapsed,
                "plain text documents were changed into Markdown");
        }

        private static string Text(FlowDocument document) => new TextRange(document.ContentStart, document.ContentEnd).Text;
        private static string Text(TextElement element) => new TextRange(element.ContentStart, element.ContentEnd).Text;
    }
}
