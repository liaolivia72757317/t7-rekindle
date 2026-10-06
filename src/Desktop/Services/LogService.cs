using System;
using System.Globalization;
using System.Text.RegularExpressions;
using NLog;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LogService
    {
        private static readonly Regex RecordPattern = new Regex(
            @"\A(?<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})  " +
            @"(?<level>INFO|WARNING|ERROR)  \[(?<source>[^\]\r\n]+)\] (?<message>.*)\z",
            RegexOptions.CultureInvariant | RegexOptions.Singleline);
        private readonly Logger _logger = LogManager.GetCurrentClassLogger();

        internal void WriteRecord(string text, Exception error = null)
        {
            var match = RecordPattern.Match(text);
            if (!match.Success || !DateTime.TryParseExact(match.Groups["timestamp"].Value,
                "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var timestamp))
            {
                _logger.Log(new LogEventInfo(LogLevel.Info, _logger.Name, text) { Exception = error });
                return;
            }

            var level = match.Groups["level"].Value == "ERROR" ? LogLevel.Error
                : match.Groups["level"].Value == "WARNING" ? LogLevel.Warn : LogLevel.Info;
            var logger = LogManager.GetLogger(match.Groups["source"].Value);
            logger.Log(new LogEventInfo(level, logger.Name, match.Groups["message"].Value)
            {
                TimeStamp = timestamp,
                Exception = error
            });
        }

        public void Info(string message)
        {
            _logger.Info(message);
        }

        public void Error(string message, System.Exception error)
        {
            _logger.Error(error, message);
        }
    }
}
