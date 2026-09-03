"""Config loading: shared common.yaml plus an optional per-product-type overlay."""

from __future__ import annotations

from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if k == "extra_fields":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(config_dir, product_type: str) -> dict:
    config_dir = Path(config_dir)
    common_path = config_dir / "common.yaml"
    if not common_path.exists():
        raise ConfigError(f"missing {common_path}")
    cfg = yaml.safe_load(common_path.read_text(encoding="utf-8"))

    # PyYAML resolves bare yes/no to booleans; we want the German strings.
    cfg["yes"] = str(cfg.get("yes", "Ja")) if not isinstance(cfg.get("yes"), bool) else "Ja"
    cfg["no"] = str(cfg.get("no", "Nein")) if not isinstance(cfg.get("no"), bool) else "Nein"

    overlay_path = config_dir / "product_types" / f"{product_type}.yaml"
    if overlay_path.exists():
        overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
        extra = overlay.get("extra_fields") or []
        prepend = overlay.get("item_form_keywords_prepend") or []
        cfg = _deep_merge(cfg, overlay)
        cfg["fields"] = list(cfg["fields"]) + list(extra)
        if prepend:
            # Product-type-specific form rules win over the shared ones, so a
            # "gel cream" listed as a moisturiser resolves to Creme, while the
            # same word in a styling product still resolves to Gel.
            cfg["item_form_keywords"] = list(prepend) + list(cfg.get("item_form_keywords", []))
        cfg["_overlay"] = str(overlay_path.name)
    else:
        cfg["_overlay"] = None
        cfg["_overlay_missing"] = product_type
    return cfg


def available_product_types(config_dir):
    d = Path(config_dir) / "product_types"
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []
