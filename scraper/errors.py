from .enums import ResultStatus


class ScrapeError(Exception):
    status = ResultStatus.ERROR
    retryable = False


class NetworkError(ScrapeError):
    status = ResultStatus.NETWORK
    retryable = True


class FetchTimeout(ScrapeError):
    status = ResultStatus.TIMEOUT
    retryable = True


class BlockedError(ScrapeError):
    """Captcha, 403/429 or an HTML page where JSON was expected. Never retried or bypassed."""

    status = ResultStatus.BLOCKED


class LayoutChanged(ScrapeError):
    """The source answered, but not in the shape the adapter knows."""

    status = ResultStatus.LAYOUT_CHANGED


class ParseError(ScrapeError):
    status = ResultStatus.INVALID


class BudgetExhausted(ScrapeError):
    status = ResultStatus.SKIPPED
