from __future__ import annotations

import unittest
from datetime import timedelta

from services.umil.app.ticker_book import LiveTickerBook
from services.umil.tests.fixtures import NOW, ticker


class LiveTickerBookTest(unittest.TestCase):
    def test_out_of_order_events_are_rejected_and_counted(self):
        book = LiveTickerBook()
        current = ticker("BTCUSDT", 101, 100, 10, event_time=NOW)
        stale = ticker("BTCUSDT", 99, 100, 10, event_time=NOW - timedelta(seconds=1))
        self.assertEqual(book.apply((current,), received_at=NOW), 1)
        self.assertEqual(book.apply((stale,), received_at=NOW), 0)
        self.assertEqual(book.snapshot()[0].last_price, 101)
        self.assertEqual(book.out_of_order_events, 1)


if __name__ == "__main__":
    unittest.main()
