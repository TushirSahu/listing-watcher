import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml

MIN_INTERVAL = timedelta(minutes=15)
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Watch:
    name: str
    site: str
    want: str
    max_price: int | None
    every: timedelta
    max_results: int = 10
    open_listings: int = 5


def parse_interval(s: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([mhd])", str(s).strip())
    if not m:
        raise ConfigError(f"bad interval {s!r}: use e.g. 30m, 2h, 1d")
    delta = timedelta(**{_UNITS[m[2]]: int(m[1])})
    if delta < MIN_INTERVAL:
        raise ConfigError(f"interval {s!r} too short: minimum is 15m")
    return delta


def load_watches(path: Path) -> list[Watch]:
    try:
        data = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    raw = data.get("watches") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path}: expected a non-empty 'watches:' list")

    watches, names = [], set()
    for i, item in enumerate(raw):
        label = item.get("name", f"#{i + 1}") if isinstance(item, dict) else f"#{i + 1}"
        try:
            if not isinstance(item, dict):
                raise ConfigError("must be a mapping")
            missing = [k for k in ("name", "site", "want") if not item.get(k)]
            if missing:
                raise ConfigError(f"missing {', '.join(missing)}")
            max_price = item.get("max_price")
            w = Watch(
                name=str(item["name"]),
                site=str(item["site"]),
                want=str(item["want"]),
                max_price=int(max_price) if max_price is not None else None,
                every=parse_interval(item.get("every", "2h")),
                max_results=int(item.get("max_results", 10)),
                open_listings=int(item.get("open_listings", 5)),
            )
        except (ConfigError, ValueError, TypeError) as e:
            raise ConfigError(f"watch {label}: {e}") from e
        if w.name in names:
            raise ConfigError(f"watch {w.name}: duplicate name")
        names.add(w.name)
        watches.append(w)
    return watches
