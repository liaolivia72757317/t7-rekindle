using System.Text.RegularExpressions;

namespace T7.Rekindle.Desktop.Services
{
    internal static class DiagnosticSanitizer
    {
        internal static string Redact(string text)
        {
            var value = text ?? string.Empty;
            value = Regex.Replace(value, @"(?i)\b[A-Z]:\\Users\\[^\\\s]+", @"C:\Users\USER");
            value = Regex.Replace(value, @"(?i)(?:[A-Z]:\\|\\\\)[^\r\n\""<>|]*", "[本地路径]");
            value = Regex.Replace(value, @"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]");
            value = Regex.Replace(value, @"(?i)\b(token|password|secret|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+", "$1=[REDACTED]");
            return value;
        }
    }
}
