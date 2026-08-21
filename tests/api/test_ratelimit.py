from astor.api.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_allows_up_to_the_limit_then_blocks():
    lim = SlidingWindowLimiter(limit=3, window_seconds=60.0, clock=FakeClock())
    assert [lim.allow("shop-a") for _ in range(3)] == [True, True, True]
    assert lim.allow("shop-a") is False


def test_window_slides_open_again():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=2, window_seconds=60.0, clock=clock)
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is False
    clock.advance(61)
    assert lim.allow("shop-a") is True


def test_partial_window_expiry_frees_exactly_one_slot():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=2, window_seconds=60.0, clock=clock)
    lim.allow("shop-a")
    clock.advance(30)
    lim.allow("shop-a")
    clock.advance(31)          # first hit is now outside the window, second is not
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is False


def test_keys_are_independent():
    lim = SlidingWindowLimiter(limit=1, window_seconds=60.0, clock=FakeClock())
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is False
    assert lim.allow("shop-b") is True
