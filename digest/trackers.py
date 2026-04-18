import re
import threading


class CostTracker:
    _SONNET_IN  = 3.00 / 1_000_000
    _SONNET_OUT = 15.0 / 1_000_000
    _HAIKU_IN   = 0.80 / 1_000_000
    _HAIKU_OUT  = 4.00 / 1_000_000

    def __init__(self, limit: float = 0.80):
        self.limit  = limit
        self._in    = 0
        self._out   = 0
        self._cost  = 0.0
        self._lock  = threading.Lock()

    def add(self, usage, model: str = "sonnet") -> None:
        is_haiku = "haiku" in model
        cost = (
            usage.input_tokens  * (self._HAIKU_IN  if is_haiku else self._SONNET_IN) +
            usage.output_tokens * (self._HAIKU_OUT if is_haiku else self._SONNET_OUT)
        )
        with self._lock:
            self._in   += usage.input_tokens
            self._out  += usage.output_tokens
            self._cost += cost

    @property
    def cost(self) -> float:
        return self._cost

    @property
    def exceeded(self) -> bool:
        return self._cost >= self.limit

    def summary(self) -> str:
        return (
            f"${self._cost:.4f} / ${self.limit:.2f} limit  "
            f"({self._in:,} in + {self._out:,} out tokens)"
        )


class CoverageTracker:
    """Thread-safe deduplication for article titles and URLs."""

    def __init__(self):
        self._lock         = threading.Lock()
        self._seen_titles: set[str] = set()
        self._seen_urls:   set[str] = set()
        self._freq:        dict[str, int] = {}

    def register(self, title: str, url: str) -> bool:
        """Return True if this article is new; False if it's a duplicate."""
        title_key = re.sub(r"\W+", "", title.lower())[:55]
        url_key   = url.rstrip("/").lower()[:100]
        with self._lock:
            self._freq[url_key] = self._freq.get(url_key, 0) + 1
            if title_key in self._seen_titles or url_key in self._seen_urls:
                return False
            self._seen_titles.add(title_key)
            self._seen_urls.add(url_key)
        return True

    def frequency(self, url: str) -> int:
        url_key = url.rstrip("/").lower()[:100]
        return self._freq.get(url_key, 1)
