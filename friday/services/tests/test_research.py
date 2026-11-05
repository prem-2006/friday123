"""Background research reports, with pretend pages and a pretend model."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest

from friday_services.config import Settings
from friday_services.research import (
    Researcher,
    ResearchError,
    clean_citations,
    obscura_fetch,
    pick_pages,
    readable,
    search_results,
    split_spoken,
)


def ddg(url: str, title: str) -> str:
    link = f"https://duckduckgo.com/l/?uddg={quote(url, safe='')}&rut=abc"
    return f"{link}\t{title}\n{link}\n{link}\t{url}\n"


PAGE = "Scooters cost about one lakh rupees in India. " * 20


def test_reads_search_results_and_leaves_out_what_cannot_be_read():
    dump = (
        "https://html.duckduckgo.com/html/\n"
        + ddg("https://a.com/one", "One")
        + ddg("https://www.youtube.com/watch?v=1", "A video")
        + ddg("https://duckduckgo.com/y.js", "An ad")
    )
    assert search_results(dump) == [("https://a.com/one", "One")]


def test_takes_one_page_per_site_from_each_search_in_turn():
    first = [("https://a.com/1", "A1"), ("https://a.com/2", "A2"), ("https://b.com/", "B")]
    second = [("https://www.a.com/3", "A3"), ("https://c.com/", "C")]
    assert [url for url, _ in pick_pages([first, second], 3)] == [
        "https://a.com/1",
        "https://c.com/",
        "https://b.com/",
    ]


def test_a_page_that_refuses_the_browser_is_not_used():
    assert readable(PAGE)
    assert not readable("too short")
    assert not readable("Our systems have detected unusual traffic from your network. " * 6)


def test_spoken_lines_and_made_up_citations_are_handled():
    assert split_spoken("## Report [1]\nSPOKEN: Scooters **cost** a lakh [1].") == (
        "## Report [1]",
        "Scooters cost a lakh .",
    )
    assert split_spoken("Only a report") == ("Only a report", "")
    assert clean_citations("Yes [1], no [7].", 2) == "Yes [1], no ."
    assert clean_citations("Both [1, 2], some [2, 7], none [8, 9].", 2) == (
        "Both [1, 2], some [2], none ."
    )
    assert split_spoken("R\nSPOKEN: A lakh [1, 2].")[1] == "A lakh ."


def researcher(tmp_path, pages=None):
    said: list[str] = []

    async def fetch(url: str, dump: str) -> str:
        if dump == "links":
            return ddg("https://a.com/one", "One") + ddg("https://b.com/two", "Two")
        return (pages or {}).get(url, PAGE)

    async def ask(system: str, user: str, tokens: int) -> str | None:
        if tokens < 500:
            return "scooter prices india"
        return "## Prices\nAbout a lakh [1] [9].\nSPOKEN: About a lakh."

    return (
        Researcher(
            Settings.from_env({}, data_dir=tmp_path),
            announce=said.append,
            fetch=fetch,
            ask=ask,
            folder=tmp_path / "Research",
        ),
        said,
    )


def test_writes_the_report_with_its_sources_and_says_it_is_ready(tmp_path):
    research, said = researcher(tmp_path)

    async def run():
        assert research.start("Electric scooters: India?")
        assert research.busy
        assert not research.start("Something else")  # one job at a time
        await research._task

    asyncio.run(run())
    report = research.latest()
    text = report.read_text(encoding="utf-8")
    assert report.parent == tmp_path / "Research"
    assert report.name.endswith("Electric scooters India.md")
    assert "About a lakh [1] ." in text
    assert "1. [One](https://a.com/one)" in text and "2. [Two](https://b.com/two)" in text
    assert "SPOKEN" not in text
    assert said == ["Your research report on Electric scooters: India? is ready. About a lakh."]


def test_says_so_when_no_page_could_be_read(tmp_path):
    research, _ = researcher(tmp_path, pages={"https://a.com/one": "no", "https://b.com/two": ""})
    with pytest.raises(ResearchError, match="could not read"):
        asyncio.run(research.research("scooters"))
    assert research.latest() is None


def test_a_stop_saves_nothing_and_says_nothing(tmp_path):
    research, said = researcher(tmp_path)
    research._stopped = lambda: True

    async def run():
        research.start("scooters")
        await research._task

    asyncio.run(run())
    assert research.latest() is None and said == []


def test_only_web_addresses_reach_the_browser(tmp_path):
    assert asyncio.run(obscura_fetch(tmp_path / "missing.exe", "--eval=x", "text")) == ""
