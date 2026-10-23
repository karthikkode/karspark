from karspark.loopguard import LoopGuard


def feed(g, text, step=37):
    return any([g.feed(text[i:i + step]) for i in range(0, len(text), step)])


def test_trips_on_an_exact_loop_and_names_the_block():
    g = LoopGuard()
    assert feed(g, "Some honest reasoning first. " * 3 + "Wait, recheck the angle. " * 300)
    assert g.block == len("Wait, recheck the angle. ") and "repeating itself" in g.reason()


def test_long_varied_prose_and_tables_pass():
    prose = " ".join(f"Line {i}: the surveyor measured bearing {i * 7 % 360} at chain {i}." for i in range(2000))
    assert not feed(LoopGuard(), prose)
    table = "".join(f"| {i} | row {i} | {i * i} |\n" for i in range(2000))
    assert not feed(LoopGuard(), table)


def test_a_short_repeat_is_fine():
    assert not feed(LoopGuard(), "intro " + "ha" * 1000 + " and then the story goes on " + "x" * 10)
