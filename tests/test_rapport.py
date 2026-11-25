import dataclasses
import re

import pandas as pd

from tuinscan.stappen import stap_rapport, stap_score, stap_tuinen


def _bouw(mw, wijzig=None):
    cfg = dataclasses.replace(
        mw.cfg, tuin=dataclasses.replace(mw.cfg.tuin, delen=["voor", "achter"])
    )
    stap_tuinen(cfg, mw.mappen)
    stap_score(cfg, mw.mappen)
    pad = mw.mappen.ronde_dir / "scores.parquet"
    sc = pd.read_parquet(pad)
    if wijzig:
        nieuw = wijzig(sc)
        sc = sc if nieuw is None else nieuw
        sc.to_parquet(pad, index=False)
    html = stap_rapport(cfg, mw.mappen).read_text(encoding="utf-8")
    return html, sc


def test_sorted_per_wijk(miniwijk):
    def wijzig(sc):
        sc["wijk"] = "A"
        sc["score"] = [10.0 + 20 * i for i in range(len(sc))]

    html, sc = _bouw(miniwijk, wijzig)
    volgorde = [html.index(f'<td class="vhe">{v}</td>') for v in
                sc.sort_values("score", ascending=False)["vhe_nummer"]]  # fmt: skip
    assert volgorde == sorted(volgorde) and len(volgorde) >= 2


def test_no_external_urls(miniwijk):
    html, _ = _bouw(miniwijk)
    assert "http://" not in html and "https://" not in html
    assert "Signaallijst – de wijkbeheerder beslist altijd zelf ter plekke." in html


def test_thumbnails_embedded(miniwijk):
    html, sc = _bouw(miniwijk)
    uit = list((miniwijk.mappen.ronde_dir / "uitsneden").glob("*_thumb_*.jpg"))
    assert uit and html.count("data:image/jpeg;base64,") == len(uit)
    assert len(sc) >= 1


def test_missing_signal_badge(miniwijk):
    def wijzig(sc):
        sc.at[0, "ontbrekend"] = ["S1"]

    html, _ = _bouw(miniwijk, wijzig)
    assert "S1 ontbreekt" in html


def test_html_escapes_address(miniwijk):
    def wijzig(sc):
        sc.at[0, "adres"] = "<b>x</b>"

    html, _ = _bouw(miniwijk, wijzig)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


def test_empty_scores_renders_empty_state(miniwijk):
    def wijzig(sc):
        return sc.iloc[0:0]

    html, _ = _bouw(miniwijk, wijzig)
    assert "Geen scores" in html


def test_foutenlijst_summary_without_addresses(miniwijk):
    html, _ = _bouw(miniwijk)
    assert "Foutenlijst" in html and re.search(r"regels? in foutenlijst", html)


def test_lean_caption_under_thumbnails(miniwijk):
    html, _ = _bouw(miniwijk)
    assert "zomerfoto niet loodrecht; daken kunnen meters over de tuin vallen" in html


def test_report_lists_ahn_kaartbladen(miniwijk):
    html, _ = _bouw(miniwijk)
    assert "AHN-kaartbladen: synthetisch" in html


def test_report_shows_contribution_per_signal(miniwijk):
    def wijzig(sc):
        for k in ("S1", "S2", "S3", "S4a", "S5"):
            sc[f"bijdrage_{k}"] = 0.0
        sc.loc[sc.index[0], "bijdrage_S4a"] = 17.5
        sc.loc[sc.index[0], "bijdrage_S2"] = 3.25

    html, _ = _bouw(miniwijk, wijzig)
    assert re.findall(r"Bijdrage: [^<]*", html) in (["Bijdrage: S4a 17.5, S2 3.2"],
                                                     ["Bijdrage: S4a 17.5, S2 3.3"])  # fmt: skip
