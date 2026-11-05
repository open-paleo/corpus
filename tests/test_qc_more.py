"""qc garbage, qc gutter, qc quotes, qc specimen-ids and fetch retry-unpaywall."""
import pytest


def test_garbage_score_separates_damaged_from_clean_text():
    import score_ocr_garbage as tool
    clean = ("The skull of this ankylosaur preserves the quadrate and the pterygoid. " * 30)
    damaged = ("The 8kull of thi8 Ankylo8aurus preserve8 the quadrat8 and '1'he pterygoid. " * 30)
    assert not tool.flagged(tool.score(clean))
    assert tool.flagged(tool.score(damaged))
    assert tool.score(damaged)["mixed_k"] > tool.score(clean)["mixed_k"]


def test_gutter_needs_ascending_line_numbers():
    import audit_gutter_manuscripts as tool
    gutter = ["\n".join(f"{n} line of manuscript text number {n}" for n in range(1 + 40 * p, 41 + 40 * p))
              for p in range(3)]
    table = ["\n".join(f"{n} 12.5 3.4 0.8" for n in (5, 3, 9, 1, 7) * 10) for _ in range(3)]
    assert tool.has_gutter(gutter)
    assert not tool.has_gutter(table)


def test_quotes_must_appear_in_order():
    import verify_audit_quotes as tool
    body = tool.norm("The holotype, a partial skull, was collected in 1923 from the Djadokhta Formation.")
    assert tool.contains(body, "a partial skull, was collected in 1923")
    assert tool.contains(body, "The holotype, a partial ... from the Djadokhta Formation")
    assert not tool.contains(body, "from the Djadokhta Formation ... The holotype, a partial")
    assert not tool.contains(body, "a complete skeleton was collected")


def test_specimen_id_reply_parsing():
    import scan_specimen_ids as tool
    assert callable(tool.parse_reply)


@pytest.mark.parametrize("flag, expected", [([], False), (["--browser-ua"], True)])
def test_retry_sends_a_browser_user_agent_only_when_asked(monkeypatch, flag, expected):
    import retry_unpaywall as tool
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        raise SystemExit(0)          # stop before any real work

    monkeypatch.setattr(tool.http, "get", fake_get)
    monkeypatch.setattr(tool, "manifest_commit", lambda manifest: None)
    monkeypatch.setattr(tool, "manifest_load", lambda: {"a2000a": {
        "doi": "10.1/x", "acquisition": {"source": "unpaywall_dl_failed", "url": "https://e.org/a.pdf"},
        "conversion": {}}})
    monkeypatch.setattr("sys.argv", ["retry_unpaywall.py", *flag])
    with pytest.raises(SystemExit):
        tool.main()
    assert seen["browser_ua"] is expected
