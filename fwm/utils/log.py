import logging
import sys


class Logger:
    class ColorFormatter(logging.Formatter):
        plain_format = "%(message)s"
        detailed_format = "%(levelname)-8s - %(message)s (%(filename)s:%(lineno)d)"

        def __init__(self, color: bool = True):
            """:param color: Color the messages: for a terminal, not for a log file."""
            super().__init__()
            if color:
                # imported when the first message is logged, not with the module: most commands never log
                import colorama
                grey = colorama.Fore.WHITE + colorama.Style.DIM
                yellow = colorama.Fore.YELLOW
                red = colorama.Fore.RED
                bold_red = colorama.Fore.RED + colorama.Style.BRIGHT
                reset = colorama.Style.RESET_ALL
            else:
                grey = yellow = red = bold_red = reset = ""
            self.FORMATS = {
                logging.DEBUG: grey + self.detailed_format + reset,
                # logging.INFO: white + self.plain_format + reset,
                logging.INFO: self.plain_format,
                logging.WARNING: yellow + self.detailed_format + reset,
                logging.ERROR: red + self.detailed_format + reset,
                logging.CRITICAL: bold_red + self.detailed_format + reset
            }

        def format(self, record):
            log_fmt = self.FORMATS.get(record.levelno)
            formatter = logging.Formatter(log_fmt)
            return formatter.format(record)

    __logger = None

    @classmethod
    def get_logger(cls) -> logging.Logger:
        if cls.__logger is not None:
            return cls.__logger
        # fwm's own logger, so that the debug logs of the libraries (e.g. peewee's queries) stay off
        logger = logging.getLogger("fwm")
        logger.setLevel(logging.DEBUG)

        # every message goes to stderr: stdout carries the commands' output (e.g. --json), which scripts parse
        stderr_stream = logging.StreamHandler(stream=sys.stderr)
        stderr_stream.setLevel(logging.DEBUG)
        stderr_stream.setFormatter(cls.ColorFormatter(color=sys.stderr.isatty()))
        logger.addHandler(stderr_stream)

        cls.__logger = logger
        return logger

    @classmethod
    def set_level(cls, level: int) -> None:
        logger = cls.get_logger()
        logger.setLevel(level)
        for handler in logger.handlers:
            handler.setLevel(level)


def main():
    Logger.set_level(logging.DEBUG)
    logger = Logger.get_logger()
    # Logger.set_level(logging.INFO)

    logger.debug("debug message")
    logger.info("info message")
    logger.warning("warning message")
    logger.error("error message")
    logger.critical("critical message")


if __name__ == '__main__':
    main()
