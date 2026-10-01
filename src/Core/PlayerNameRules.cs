using System;
using System.Linq;
using System.Text;

namespace T7.Rekindle.Core
{
    public static class PlayerNameRules
    {
        public const string DefaultName = "吃我一记流星锤";
        public const int MaximumBytes = 31;
        private static readonly Encoding Gbk = Encoding.GetEncoding(936,
            EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);

        public static string Validate(string value)
        {
            var name = (value ?? string.Empty).Trim();
            if (name.Length == 0) return "请输入玩家名称。";
            if (name.Any(char.IsControl)) return "名称中请勿使用换行、制表符或其他控制字符。";
            try
            {
                var bytes = Gbk.GetBytes(name);
                if (Gbk.GetString(bytes) != name || name.Contains("€") || name.Any(ch => ch >= '\uE000' && ch <= '\uF8FF'))
                    return "名称包含游戏 GBK 编码不支持的字符。";
                if (bytes.Length > MaximumBytes) return "名称过长：GBK 编码最多 31 字节（汉字通常占 2 字节）。";
            }
            catch (EncoderFallbackException) { return "名称包含游戏 GBK 编码不支持的字符，请移除 emoji 等字符。"; }
            return string.Empty;
        }
    }
}
