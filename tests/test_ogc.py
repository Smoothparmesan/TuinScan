from urllib.parse import parse_qs, urlparse

import requests
import responses

from tuinscan.pdok import ogc

BASIS = "https://example.test/ogc"
URL = f"{BASIS}/collections/pand/items"


def _feat(i, x=1.0, **props):
    return {
        "type": "Feature",
        "id": f"uuid-{i}",
        "geometry": {"type": "Point", "coordinates": [x, 2.0]},
        "properties": props,
    }


def _fc(feats, next_url=None):
    links = [{"rel": "self", "href": URL}]
    if next_url:
        links.append({"rel": "next", "href": next_url})
    return {"type": "FeatureCollection", "features": feats, "links": links}


@responses.activate
def test_follows_next_links():
    responses.get(URL, json=_fc([_feat(1), _feat(2)], next_url=f"{URL}?page=2"))
    responses.get(f"{URL}?page=2", json=_fc([_feat(3)]))
    gdf = ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    assert len(gdf) == 3
    assert gdf.crs.to_epsg() == 28992


@responses.activate
def test_sends_bbox_and_crs_params():
    responses.get(URL, json=_fc([_feat(1)]))
    ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    q = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert q["bbox"] == ["1,2,3,4"]
    assert q["bbox-crs"] == [ogc.RD]
    assert q["crs"] == [ogc.RD]


@responses.activate
def test_no_address_params():
    responses.get(URL, json=_fc([]))
    ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    q = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert set(q) <= {"bbox", "bbox-crs", "crs", "limit", "f"}


@responses.activate
def test_retry_on_503(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ogc.time, "sleep", sleeps.append)
    responses.get(URL, status=503)
    responses.get(URL, json=_fc([_feat(1)]))
    gdf = ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    assert len(gdf) == 1
    assert len(responses.calls) == 2
    assert sleeps == [2]


@responses.activate
def test_gives_up_after_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ogc.time, "sleep", sleeps.append)
    responses.get(URL, status=503)
    try:
        ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("expected HTTPError")
    assert sleeps == [2, 4, 8]


@responses.activate
def test_empty_collection():
    responses.get(URL, json=_fc([]))
    gdf = ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    assert len(gdf) == 0
    assert gdf.crs.to_epsg() == 28992
    assert "geometry" in gdf.columns


@responses.activate
def test_feature_id_kept():
    responses.get(
        URL, json=_fc([_feat(1, href=["http://a/b/1", "http://a/b/2"], naam="x")])
    )
    gdf = ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    assert gdf.loc[0, "feature_id"] == "uuid-1"
    assert gdf.loc[0, "href"] == ["http://a/b/1", "http://a/b/2"]
    assert isinstance(gdf.loc[0, "href"], list)


@responses.activate
def test_bbox_keeps_fractional_metres():
    responses.get(URL, json=_fc([]))
    ogc.haal_features(BASIS, "pand", (121394.123, 1, 487000.5, 487100.0004), requests.Session())
    q = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert q["bbox"] == ["121394.123,1,487000.5,487100"]


@responses.activate
def test_retry_on_connection_error(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ogc.time, "sleep", sleeps.append)
    responses.get(URL, body=requests.ConnectionError("weg"))
    responses.get(URL, body=requests.Timeout("traag"))
    responses.get(URL, json=_fc([_feat(1)]))
    gdf = ogc.haal_features(BASIS, "pand", (1, 2, 3, 4), requests.Session())
    assert len(gdf) == 1
    assert sleeps == [2, 4]
