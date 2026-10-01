using NLog;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LogService
    {
        private readonly Logger _logger = LogManager.GetCurrentClassLogger();

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
