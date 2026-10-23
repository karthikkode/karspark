from pathlib import Path

import pytest

from karspark import config as cfgmod

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def cfg():
    return cfgmod.load(REPO)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock():
    return Clock()
