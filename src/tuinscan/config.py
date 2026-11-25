"""Configuratie: getypeerde dataclasses, laden, valideren en parameterset-hash."""

import hashlib
import json
import types
import typing
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import yaml


class ConfigFout(Exception):
    """Ongeldige configuratie; de melding noemt de YAML-sleutel."""


MAX_PANDMARGE_M = 10.0  # rasters berekent de afstand tot panden tot zover

# Optionele drempels per luchtfotolaag (luchtfoto.lagen.<laag>.<drempel>); zonder waarde
# geldt signalen.<drempel>. Elke drempel hoort bij de laag waarvan hij de pixels beoordeelt.
LAAG_DREMPELS = {
    "ndvi_drempel": ("cir_nu", "cir_vorig"),
    "exg_drempel": ("zomer_nu", "zomer_vorig"),
}


@dataclass(frozen=True)
class Gebied:
    naam: str
    bbox_rd: tuple[float, float, float, float]
    buffer_m: float


@dataclass(frozen=True)
class MappenCfg:
    invoer: str
    cache: str
    uitvoer: str


@dataclass(frozen=True)
class Tobias:
    bestand: str
    blad: str | None
    kopregel: int | None
    kolommen_bestand: str
    filters: dict[str, list]


@dataclass(frozen=True)
class Pdok:
    bag_url: str
    brk_url: str
    bgt_url: str
    collecties: dict[str, str]
    velden: dict[str, str]
    voetpad_functies: list[str]
    rijbaan_functies: list[str]  # alleen voor `kalibreer` (verharding als referentie)
    openbaar_groen_fysiek: list[str]
    workers: int


@dataclass(frozen=True)
class Luchtfoto:
    rgb_wmts: str
    cir_wmts: str
    matrixset: str
    lagen: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Ahn:
    versie: str
    dtm_index: str
    dsm_index: str
    url_veld: str
    vorige: dict[str, Any] | None


@dataclass(frozen=True)
class Tuin:
    delen: list[str]
    max_diepte_m: float
    voorzijde_zoekafstand_m: float
    min_oppervlak_m2: float


@dataclass(frozen=True)
class Signalen:
    resolutie_m: float
    exg_drempel: float
    ndvi_drempel: float
    schaduw_helderheid: float
    s2_hoogte_m: float
    s3_hoogte_m: float
    s3_buffer_m: float
    s4a_hoogte_m: float
    s4a_min_m2: float
    s4a_vorige_max_m: float
    s4a_standaardberging_max_m2: float
    foto_pandmarge_m: float  # S1/S5: pixels zo dicht bij een BAG-pand tellen niet (0 = uit)
    s4a_groen_marge_m: float  # begroeiing zoveel verwijden vóór "niet groen"
    s4a_max_hoogte_m: float  # hoger = boomkruin of dak, geen tuinobject
    s4a_pandmarge_m: float  # hoogte-halo langs BAG-panden negeren (0 = uit)
    s4a_opening_px: int  # morfologische opening (straal in pixels) tegen schuttinglijnen


@dataclass(frozen=True)
class ScoreRegel:
    gewicht: float
    onder: float
    boven: float


@dataclass(frozen=True)
class Terugkoppeling:
    top_n: int
    controlegroep_n: int
    seed: int
    oordeel: list[str]
    oorzaak: list[str]
    actie: list[str]


@dataclass(frozen=True)
class Config:
    gebied: Gebied
    mappen: MappenCfg
    tobias: Tobias
    pdok: Pdok
    luchtfoto: Luchtfoto
    ahn: Ahn
    tuin: Tuin
    signalen: Signalen
    score: dict[str, ScoreRegel]
    terugkoppeling: Terugkoppeling


def _past(w: Any, t: Any) -> bool:
    if t is Any:
        return True
    if t is None or t is type(None):
        return w is None
    o = typing.get_origin(t)
    if o in (typing.Union, types.UnionType):
        return any(_past(w, a) for a in typing.get_args(t))
    if o in (list, tuple):
        a = typing.get_args(t)
        item = a[0] if a else Any
        return isinstance(w, (list, tuple)) and all(_past(x, item) for x in w)
    if o is dict:
        k, v = typing.get_args(t) or (Any, Any)
        return isinstance(w, dict) and all(_past(a, k) and _past(b, v) for a, b in w.items())
    if t is bool:
        return isinstance(w, bool)
    if t is int:
        return isinstance(w, int) and not isinstance(w, bool)
    if t is float:
        return isinstance(w, (int, float)) and not isinstance(w, bool)
    return isinstance(w, t)


def _bouw(cls, data: Any, sleutel: str):
    if not isinstance(data, dict):
        raise ConfigFout(f"{sleutel}: verwacht een tabel, kreeg {type(data).__name__}")
    namen = {f.name for f in fields(cls)}
    ontbreekt = sorted(namen - data.keys())
    if ontbreekt:
        raise ConfigFout(f"{sleutel}.{ontbreekt[0]}: ontbreekt")
    onbekend = sorted(data.keys() - namen)
    if onbekend:
        raise ConfigFout(f"{sleutel}.{onbekend[0]}: onbekende sleutel")
    hints = typing.get_type_hints(cls)
    for naam in namen:
        if not _past(data[naam], hints[naam]):
            raise ConfigFout(f"{sleutel}.{naam}: ongeldig type of waarde ({data[naam]!r})")
    return cls(**data)


def _valideer(cfg: Config) -> None:
    b = cfg.gebied.bbox_rd
    if len(b) != 4 or not all(isinstance(v, (int, float)) for v in b):
        raise ConfigFout("gebied.bbox_rd: verwacht 4 getallen [xmin, ymin, xmax, ymax]")
    if not (b[0] < b[2] and b[1] < b[3]):
        raise ConfigFout("gebied.bbox_rd: moet xmin < xmax en ymin < ymax hebben")
    if not cfg.score:
        raise ConfigFout("score: minstens één signaal vereist")
    for naam, r in cfg.score.items():
        if not r.onder < r.boven:
            raise ConfigFout(f"score.{naam}: onder moet kleiner zijn dan boven")
    totaal = sum(r.gewicht for r in cfg.score.values())
    if abs(totaal - 100) > 1e-9:
        raise ConfigFout(f"score: gewichten moeten samen 100 zijn, nu {totaal:g}")
    v = cfg.ahn.vorige
    if v is not None:
        for k in ("versie", "dtm_index", "dsm_index"):
            if not isinstance(v, dict) or not v.get(k):
                raise ConfigFout(f"ahn.vorige.{k}: ontbreekt")
    if not cfg.tuin.delen or not set(cfg.tuin.delen) <= {"voor", "achter"}:
        raise ConfigFout("tuin.delen: alleen 'voor' en/of 'achter' toegestaan")
    s = cfg.signalen
    for naam in ("foto_pandmarge_m", "s4a_pandmarge_m"):
        if not 0 <= getattr(s, naam) <= MAX_PANDMARGE_M:
            raise ConfigFout(f"signalen.{naam}: verwacht 0 t/m {MAX_PANDMARGE_M:g} m")
    if s.s4a_groen_marge_m < 0:
        raise ConfigFout("signalen.s4a_groen_marge_m: mag niet negatief zijn")
    if s.s4a_opening_px < 0:
        raise ConfigFout("signalen.s4a_opening_px: mag niet negatief zijn")
    if not s.s4a_max_hoogte_m > s.s4a_hoogte_m:
        raise ConfigFout("signalen.s4a_max_hoogte_m: moet groter zijn dan s4a_hoogte_m")
    for laag, spec in cfg.luchtfoto.lagen.items():
        for naam, lagen in LAAG_DREMPELS.items():
            if not isinstance(spec, dict) or naam not in spec:
                continue
            sleutel = f"luchtfoto.lagen.{laag}.{naam}"
            if laag not in lagen:
                raise ConfigFout(f"{sleutel}: alleen toegestaan bij {' of '.join(lagen)}")
            if not _past(spec[naam], float):
                raise ConfigFout(f"{sleutel}: verwacht een getal ({spec[naam]!r})")


def laad_config(pad: Path) -> Config:
    try:
        data = yaml.safe_load(Path(pad).read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigFout(f"{pad}: kan niet lezen ({e})") from e
    except yaml.YAMLError as e:
        raise ConfigFout(f"{pad}: ongeldige YAML ({e})") from e
    if not isinstance(data, dict):
        raise ConfigFout(f"{pad}: verwacht een tabel op hoofdniveau")
    try:
        cfg = Config(
            gebied=_bouw(Gebied, data.get("gebied"), "gebied"),
            mappen=_bouw(MappenCfg, data.get("mappen"), "mappen"),
            tobias=_bouw(Tobias, data.get("tobias"), "tobias"),
            pdok=_bouw(Pdok, data.get("pdok"), "pdok"),
            luchtfoto=_bouw(Luchtfoto, data.get("luchtfoto"), "luchtfoto"),
            ahn=_bouw(Ahn, data.get("ahn"), "ahn"),
            tuin=_bouw(Tuin, data.get("tuin"), "tuin"),
            signalen=_bouw(Signalen, data.get("signalen"), "signalen"),
            score={
                k: _bouw(ScoreRegel, v, f"score.{k}")
                for k, v in _tabel(data.get("score"), "score").items()
            },
            terugkoppeling=_bouw(Terugkoppeling, data.get("terugkoppeling"), "terugkoppeling"),
        )
    except TypeError as e:
        raise ConfigFout(f"ongeldige waarde in config ({e})") from e
    _valideer(cfg)
    return cfg


def _tabel(data: Any, sleutel: str) -> dict:
    if not isinstance(data, dict):
        raise ConfigFout(f"{sleutel}: verwacht een tabel")
    return data


def laag_drempel(cfg: Config, laag: str, naam: str) -> float:
    """Drempel `naam` voor luchtfotolaag `laag`; zonder eigen waarde signalen.<naam>."""
    spec = cfg.luchtfoto.lagen.get(laag) or {}
    waarde = spec.get(naam)
    return float(getattr(cfg.signalen, naam) if waarde is None else waarde)


def _laag_drempels(cfg: Config) -> dict[str, dict[str, float]]:
    uit: dict[str, dict[str, float]] = {}
    for naam, lagen in LAAG_DREMPELS.items():
        for laag in lagen:
            waarde = (cfg.luchtfoto.lagen.get(laag) or {}).get(naam)
            if waarde is not None:
                uit.setdefault(laag, {})[naam] = float(waarde)
    return uit


def parameterset_hash(cfg: Config) -> str:
    """Eerste 8 hex van sha256 over canonieke JSON van tuin, signalen, score en de
    drempels per luchtfotolaag (die alleen als ze gezet zijn)."""
    payload = {
        "tuin": asdict(cfg.tuin),
        "signalen": asdict(cfg.signalen),
        "score": {k: asdict(v) for k, v in cfg.score.items()},
    }
    drempels = _laag_drempels(cfg)
    if drempels:
        payload["laag_drempels"] = drempels
    canoniek = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canoniek.encode("utf-8")).hexdigest()[:8]
