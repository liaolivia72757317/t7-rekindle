using System;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Documents;

namespace T7.Rekindle.Desktop.Views
{
    // Bundled documents use headings, paragraphs, lists and inline formatting.
    internal static class MarkdownDocument
    {
        private static readonly Regex Heading = new Regex(@"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$", RegexOptions.CultureInvariant);
        private static readonly Regex ListItem = new Regex(@"^ {0,3}(?:([-+*])|([1-9][0-9]{0,8})[.)])[ \t]+(.*)$", RegexOptions.CultureInvariant);
        private static readonly Regex InlineMarkup = new Regex(
            @"\\(?<escape>[\\`*_])|(?<ticks>`+)(?<code>.+?)\k<ticks>|(?<strong>\*\*|__)(?<bold>.+?)\k<strong>|(?<emphasis>\*|_)(?<italic>.+?)\k<emphasis>",
            RegexOptions.CultureInvariant);

        internal static FlowDocument Render(string markdown)
        {
            var document = new FlowDocument { FontSize = 13, LineHeight = 22, PagePadding = new Thickness(16), TextAlignment = TextAlignment.Left };
            document.SetResourceReference(FlowDocument.FontFamilyProperty, "UiFontFamily");
            document.SetResourceReference(FlowDocument.ForegroundProperty, "TextBrush");
            Paragraph paragraph = null;
            List list = null;
            foreach (var line in (markdown ?? string.Empty).Replace("\r\n", "\n").Replace('\r', '\n').Split('\n'))
            {
                if (string.IsNullOrWhiteSpace(line))
                {
                    paragraph = null;
                    list = null;
                    continue;
                }
                var heading = Heading.Match(line);
                if (heading.Success)
                {
                    var size = new[] { 22.0, 18, 16, 14, 13, 13 }[heading.Groups[1].Length - 1];
                    var title = new Paragraph
                    {
                        FontSize = size,
                        FontWeight = FontWeights.SemiBold,
                        LineHeight = size + 8,
                        Margin = new Thickness(0, document.Blocks.Count == 0 ? 0 : 12, 0, 10)
                    };
                    AddInlines(title.Inlines, Regex.Replace(heading.Groups[2].Value.Trim(), @"[ \t]+#+$", string.Empty));
                    document.Blocks.Add(title);
                    paragraph = null;
                    list = null;
                    continue;
                }
                var item = ListItem.Match(line);
                if (item.Success)
                {
                    var marker = item.Groups[2].Success ? TextMarkerStyle.Decimal : TextMarkerStyle.Disc;
                    if (list == null || list.MarkerStyle != marker)
                    {
                        list = new List
                        {
                            MarkerStyle = marker,
                            StartIndex = item.Groups[2].Success ? int.Parse(item.Groups[2].Value, CultureInfo.InvariantCulture) : 1,
                            Padding = new Thickness(24, 0, 0, 0),
                            Margin = new Thickness(0, 0, 0, 10)
                        };
                        document.Blocks.Add(list);
                    }
                    var content = new Paragraph { Margin = new Thickness(0, 0, 0, 4) };
                    AddInlines(content.Inlines, item.Groups[3].Value);
                    list.ListItems.Add(new System.Windows.Documents.ListItem(content));
                    paragraph = null;
                    continue;
                }
                list = null;
                if (paragraph == null)
                {
                    paragraph = new Paragraph { Margin = new Thickness(0, 0, 0, 10) };
                    document.Blocks.Add(paragraph);
                }
                else paragraph.Inlines.Add(new Run(" "));
                AddInlines(paragraph.Inlines, line.Trim());
            }
            return document;
        }

        private static void AddInlines(InlineCollection inlines, string text)
        {
            var offset = 0;
            foreach (Match match in InlineMarkup.Matches(text))
            {
                if (match.Index > offset) inlines.Add(new Run(text.Substring(offset, match.Index - offset)));
                if (match.Groups["escape"].Success) inlines.Add(new Run(match.Groups["escape"].Value));
                else if (match.Groups["code"].Success)
                {
                    var code = new Run(match.Groups["code"].Value);
                    code.SetResourceReference(TextElement.FontFamilyProperty, "CodeFontFamily");
                    code.SetResourceReference(TextElement.BackgroundProperty, "AccentSoftBrush");
                    inlines.Add(code);
                }
                else
                {
                    var bold = match.Groups["bold"].Success;
                    Span emphasis = bold ? (Span)new Bold() : new Italic();
                    AddInlines(emphasis.Inlines, match.Groups[bold ? "bold" : "italic"].Value);
                    inlines.Add(emphasis);
                }
                offset = match.Index + match.Length;
            }
            if (offset < text.Length) inlines.Add(new Run(text.Substring(offset)));
        }
    }
}
