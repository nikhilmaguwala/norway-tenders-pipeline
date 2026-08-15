from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from norway_tenders.retrieval.downloader import TedXmlCacheMissError, fetch_ted_xml


def test_fetch_ted_xml_offline_cache_miss_raises_without_network(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "norway_tenders.retrieval.downloader.CACHE_DIR",
        tmp_path / "cache",
    )
    notice_id = "999999-2099"
    expected_path = tmp_path / "cache" / "ted_xml" / f"{notice_id}.xml"

    with patch("norway_tenders.retrieval.downloader.httpx.Client") as client_cls:
        with pytest.raises(TedXmlCacheMissError) as exc_info:
            fetch_ted_xml(notice_id, offline=True)

    client_cls.assert_not_called()
    err = exc_info.value
    assert err.notice_id == notice_id
    assert err.cache_path == expected_path
    assert notice_id in str(err)
    assert str(expected_path) in str(err)
    assert "build --offline" in str(err)


def test_fetch_ted_xml_offline_reads_cache_without_network(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    notice_id = "196990-2022"
    dest = cache_dir / "ted_xml" / f"{notice_id}.xml"
    dest.parent.mkdir(parents=True)
    dest.write_text("<TED_EXPORT>cached</TED_EXPORT>", encoding="utf-8")
    monkeypatch.setattr("norway_tenders.retrieval.downloader.CACHE_DIR", cache_dir)

    with patch("norway_tenders.retrieval.downloader.httpx.Client") as client_cls:
        xml = fetch_ted_xml(notice_id, offline=True)

    client_cls.assert_not_called()
    assert xml == "<TED_EXPORT>cached</TED_EXPORT>"


def test_fetch_ted_xml_online_cache_miss_fetches(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    notice_id = "196990-2022"
    monkeypatch.setattr("norway_tenders.retrieval.downloader.CACHE_DIR", cache_dir)
    monkeypatch.setattr("norway_tenders.retrieval.downloader.REQUEST_DELAY_SECONDS", 0)

    response = MagicMock()
    response.content = b"<TED_EXPORT>fetched</TED_EXPORT>"
    response.text = "<TED_EXPORT>fetched</TED_EXPORT>"
    response.raise_for_status = MagicMock()

    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response

    with patch("norway_tenders.retrieval.downloader.httpx.Client", return_value=client) as client_cls:
        xml = fetch_ted_xml(notice_id, offline=False)

    client_cls.assert_called_once()
    client.get.assert_called_once()
    assert xml == "<TED_EXPORT>fetched</TED_EXPORT>"
    assert (cache_dir / "ted_xml" / f"{notice_id}.xml").read_text(encoding="utf-8") == xml
