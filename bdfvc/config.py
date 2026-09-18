"""Config loading: shared common.yaml, an optional brand overlay, then an
optional per-product-type overlay.

    common.yaml  ->  brands/<slug>.yaml  ->  product_types/<PT>.yaml

The brand layer exists because one company can sell through several Vendor
Central accounts: Beiersdorf Cosmed (de_beauty, BEIF5) and Eucerin
(de_luxury_beauty, EUCFU) share a manufacturer, a vocabulary and a field list
but differ on vendor code, category tree and lifestyle.

That sharing is the reason brand profiles are deliberately restricted. Because
_deep_merge replaces lists wholesale, a brand file that innocently set
`brand_rules:` would silently disable the NIVEA / NIVEA MEN / NIVEA SUN split
for that run, with nothing to catch it. So a brand profile may only set the keys
in BRAND_KEYS, and anything else is a load-time error naming the offending key.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_BRAND = "beiersdorf_cosmed"

# The only keys a config/brands/*.yaml may set.
#
#   label, detect    identity and auto-detection, not mapping rules
#   vendor_code      a scalar; replacing it is the whole point
#   field_overrides  replaces named field specs, one code at a time
#   the three dicts  merge additively, so a brand adds rows and never removes
#                    another account's
#
# Everything absent from this set - manufacturer, locale, yes/no, dimension_unit,
# weight_units, content_units, brand_rules, item_form_keywords, scent_keywords,
# skin_type_*, dangerous_goods, lookups, fields - is company-wide and stays in
# common.yaml where every account gets it.
BRAND_KEYS = frozenset(
    {
        "label",
        "detect",
        "vendor_code",
        "field_overrides",
        "mgr_categories",
        "subcategory_overrides",
        "browse_nodes",
    }
)

# Keys the merge must not copy verbatim: they are instructions about the field
# list, not config values, and are applied separately.
_MERGE_SKIP = ("extra_fields", "field_overrides")


class ConfigError(Exception):
    pass


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if k in _MERGE_SKIP:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} is not valid YAML: {e}") from e


# ----------------------------------------------------------------------- brands


def _brand_dir(config_dir) -> Path:
    return Path(config_dir) / "brands"


def _load_brand(config_dir, brand: str) -> dict:
    path = _brand_dir(config_dir) / f"{brand}.yaml"
    if not path.exists():
        known = ", ".join(slug for slug, _label in available_brands(config_dir)) or "none"
        raise ConfigError(f"unknown brand {brand!r} (config/brands holds: {known})")

    profile = _read_yaml(path)
    stray = sorted(set(profile) - BRAND_KEYS)
    if stray:
        raise ConfigError(
            f"{path.name} sets {', '.join(stray)}, which a brand profile may not change. "
            f"Those rules are shared by every Beiersdorf account and belong in common.yaml. "
            f"A brand profile may only set: {', '.join(sorted(BRAND_KEYS))}."
        )
    return profile


def _apply_field_overrides(fields: list, overrides: dict, source: str) -> list:
    """Replace whole field specs by code. An override that matches nothing is an
    error - a renamed field should fail loudly, not be quietly dropped."""
    if not overrides:
        return fields
    by_code = {str(spec.get("code")): i for i, spec in enumerate(fields)}
    missing = [c for c in overrides if c not in by_code]
    if missing:
        raise ConfigError(
            f"{source} overrides field(s) not in the field list: {', '.join(sorted(missing))}"
        )
    out = list(fields)
    for code, spec in overrides.items():
        merged = dict(out[by_code[code]])
        merged.update(spec)
        merged["code"] = code
        out[by_code[code]] = merged
    return out


def available_brands(config_dir):
    """[(slug, label), ...] for the picker. Missing directory is not an error."""
    d = _brand_dir(config_dir)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")):
        if p.stem.startswith("_"):
            continue
        try:
            label = (_read_yaml(p).get("label") or p.stem).strip()
        except ConfigError:
            label = p.stem
        out.append((p.stem, label))
    # The default account first, then alphabetical.
    out.sort(key=lambda sl: (sl[0] != DEFAULT_BRAND, sl[1]))
    return out


def detect_brand(config_dir, vendor_options):
    """Which brand a template belongs to, read off its Händlercode dropdown.

    The Eucerin template offers exactly one option ('Eucerin, de_luxury_beauty,
    EUCFU'); the Cosmed templates offer five, all Beiersdorf codes and none of
    them Eucerin. So a token match against the template's own dropdown decides
    the account without guessing.

    Returns a slug, or None when nothing or more than one profile matches - the
    caller falls back to the default and lets the user pick.
    """
    options = [str(o).upper() for o in (vendor_options or [])]
    if not options:
        return None
    hits = []
    for slug, _label in available_brands(config_dir):
        try:
            token = (_load_brand(config_dir, slug).get("detect") or {}).get("vendor_token")
        except ConfigError:
            continue
        if token and any(str(token).upper() in o for o in options):
            hits.append(slug)
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------------ load


def load(config_dir, product_type: str, brand: str | None = None) -> dict:
    config_dir = Path(config_dir)
    common_path = config_dir / "common.yaml"
    if not common_path.exists():
        raise ConfigError(f"missing {common_path}")
    cfg = _read_yaml(common_path)

    # PyYAML resolves bare yes/no to booleans; we want the German strings.
    cfg["yes"] = str(cfg.get("yes", "Ja")) if not isinstance(cfg.get("yes"), bool) else "Ja"
    cfg["no"] = str(cfg.get("no", "Nein")) if not isinstance(cfg.get("no"), bool) else "Nein"

    # ---------------------------------------------------------- brand overlay
    field_overrides, brand_source = {}, None
    if _brand_dir(config_dir).is_dir():
        brand = brand or DEFAULT_BRAND
        profile = _load_brand(config_dir, brand)
        field_overrides = profile.get("field_overrides") or {}
        brand_source = f"brands/{brand}.yaml"
        cfg = _deep_merge(cfg, profile)
        cfg["_brand"] = brand
        cfg["_brand_label"] = profile.get("label") or brand
    else:
        # No brands directory (older checkout): behave exactly as before.
        cfg["_brand"] = None
        cfg["_brand_label"] = None

    # --------------------------------------------------- product-type overlay
    overlay_path = config_dir / "product_types" / f"{product_type}.yaml"
    if overlay_path.exists():
        overlay = _read_yaml(overlay_path)
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

    # Applied last so a brand's override wins over both layers' field specs,
    # including any extra_fields the product type added.
    cfg["fields"] = _apply_field_overrides(cfg["fields"], field_overrides, brand_source or "brand")
    return cfg


def available_product_types(config_dir):
    d = Path(config_dir) / "product_types"
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []
