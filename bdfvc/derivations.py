"""Named derivations referenced from config by `derive:`.

Each function takes the resolution context and returns either

    (value, provenance_note)                      - single field
    ({field_code: value, ...}, provenance_note)   - compound field group
    (None, reason)                                - unresolved, will be flagged

Nothing here invents a value. Where a rule genuinely cannot decide - `scent` is
the only one - the function says so via the `low_confidence` flag so QA can
surface it for human review instead of silently shipping a guess.
"""

from __future__ import annotations

from . import transforms as T

REGISTRY = {}


def derivation(name):
    def deco(fn):
        REGISTRY[name] = fn
        return fn

    return deco


def _keyword_pick(text, rules, default=None):
    """First rule whose `match` keywords appear in `text` wins.

    Rules are ordered, so put the specific ones first ('Zerstäuber' before 'Spray').
    Returns (candidate_list, matched_keyword | None).
    """
    hay = f" {str(text or '').lower()} "
    for rule in rules:
        for kw in rule.get("match", []):
            if kw.lower() in hay:
                cands = rule.get("candidates") or [rule.get("value")]
                return [c for c in cands if c], kw
    return (list(default) if default else []), None


# --------------------------------------------------------------------- basics


@derivation("item_name")
def d_item_name(ctx):
    title, notes = T.clean_title(ctx.record.get("title"))
    if not title:
        return None, "no Artikellangtext 1 in source"
    return title, "; ".join(notes) if notes else "copied from Artikellangtext 1"


@derivation("brand")
def d_brand(ctx):
    title = ctx.resolved_value("item_name#1.value") or ctx.record.get("title")
    gender = str(ctx.record.get("gender") or "").strip()
    brand = T.brand_from_title(title, ctx.config.get("brand_rules", []), gender=gender)
    if not brand:
        return None, f"no brand rule matches the first token of {title!r}"
    note = "first token of item name"
    if gender and gender in ctx.config.get("male_gender_values", []) and brand.endswith("MEN"):
        note += f", promoted to a men's line by Zielgruppe={gender!r}"
    return brand, note


@derivation("external_product_id")
def d_gtin(ctx):
    raw = ctx.record.get("gtin")
    gtin = T.gtin14(raw)
    if gtin is None:
        pre = ctx.record.get("gtin_padded")
        gtin = T.gtin14(pre) if pre else None
    if gtin is None:
        return None, "GTIN Stück is empty or not numeric"
    note = "padded to 14 digits"
    if not T.gtin_check_digit_ok(gtin):
        note += " (GS1 check digit FAILS - verify with BDF)"
    return gtin, note


@derivation("product_type")
def d_product_type(ctx):
    """Read the product type off the template rather than trusting config.

    Amazon writes it either as the code (SKIN_MOISTURIZER) or as the display
    label (Body Deodorant) depending on the template build, so both are offered
    and the template's own dropdown decides.
    """
    code = ctx.template.product_type
    pretty = code.replace("_", " ").title()
    return [code, pretty, ctx.template.label("product_type#1.value")], "from template sheet name"


@derivation("vendor_code")
def d_vendor_code(ctx):
    """Pick the dropdown option carrying the configured vendor code token."""
    token = str(ctx.config.get("vendor_code", "")).upper()
    options = ctx.allowed_values() or []
    hits = [o for o in options if token in str(o).upper()]
    if len(hits) == 1:
        return hits[0], f"the only template option containing {token}"
    if not hits:
        return None, f"no template option contains vendor code {token}"
    return None, f"{len(hits)} template options contain {token}; ambiguous"


# ------------------------------------------------------------------ structure


@derivation("category_chain")
def d_category(ctx):
    """product_category from the MGR, then subcategory, then browse node.

    Verified 100% stable across 58 April rows: one MGR maps to exactly one
    category/subcategory pair. The browse node is not fixed by the MGR - it is
    decided by a title keyword within the subcategory - so it is resolved
    separately by `browse_node`.
    """
    mgr = str(ctx.record.get("mgr_name") or "").strip().upper()
    table = ctx.config.get("mgr_categories", {})
    entry = table.get(mgr)
    if entry is None:
        for key, val in table.items():
            if key.endswith("*") and mgr.startswith(key[:-1].strip()):
                entry = val
                break
    if entry is None:
        return None, f"MGR Bezeichnung {mgr!r} is not in the MGR->category table"
    return (
        {
            "product_category#1.value": entry["category"],
            "product_subcategory#1.value": entry["subcategory"],
        },
        f"MGR {mgr}",
    )


@derivation("browse_node")
def d_browse_node(ctx):
    subcat = ctx.resolved_value("product_subcategory#1.value")
    rules = ctx.config.get("browse_nodes", {}).get(subcat)
    if rules is None:
        return None, f"no browse-node rule for subcategory {subcat!r}"
    if isinstance(rules, str):
        return rules, f"fixed for subcategory {subcat}"
    cands, kw = _keyword_pick(ctx.record.get("title"), rules.get("keywords", []), rules.get("default"))
    if not cands:
        return None, f"no browse-node keyword matched for subcategory {subcat!r}"
    return cands, (f"title keyword {kw!r}" if kw else f"default for {subcat}")


@derivation("item_form")
def d_item_form(ctx):
    cands, kw = _keyword_pick(
        ctx.record.get("title"), ctx.config.get("item_form_keywords", []), None
    )
    if cands:
        return cands, f"title keyword {kw!r}"

    # No form word in the name. A product carrying a dangerous-goods profile is
    # pressurised, which for this catalogue always means an aerosol.
    profile = str(ctx.record.get("dg_profile") or "").strip().upper()
    dg = ctx.config.get("dangerous_goods", {})
    fallback = ctx.config.get("item_form_when_pressurised")
    if fallback and profile in dg.get("hazardous_profiles", ["GPP"]):
        return list(fallback), f"no keyword in the name; Gefahrgut-profil {profile} implies a propellant"
    return None, "no item-form keyword in the item name"


@derivation("scent")
def d_scent(ctx):
    rules = ctx.config.get("scent_keywords", [])
    default = ctx.config.get("scent_default", [])
    cands, kw = _keyword_pick(ctx.record.get("title"), rules, default)
    if not cands:
        return None, "no scent keyword matched and no fallback configured"
    if kw is None:
        ctx.low_confidence = True
        return cands, "fallback scent - REVIEW: no keyword matched the item name"
    return cands, f"title keyword {kw!r}"


@derivation("skin_type")
def d_skin_type(ctx):
    src = ctx.record.get("skin_type")
    if src:
        cands = ctx.config.get("skin_type_lookup", {}).get(str(src).strip())
        if cands:
            return cands, f"source Hauttyp {src!r}"
    cands, kw = _keyword_pick(
        ctx.record.get("title"),
        ctx.config.get("skin_type_keywords", []),
        ctx.config.get("skin_type_default", []),
    )
    if not cands:
        return None, f"source Hauttyp {src!r} not in lookup and no title keyword"
    return cands, (f"title keyword {kw!r}" if kw else "default skin type")


# ------------------------------------------------------------------- hazardous


@derivation("dangerous_goods")
def d_dangerous_goods(ctx):
    """GPP -> Transport + UN id; NOG/blank -> Not Applicable, hazmat cleared.

    Verified against 30/30 deodorant rows with zero exceptions.
    """
    profile = str(ctx.record.get("dg_profile") or "").strip().upper()
    un = str(ctx.record.get("un_number") or "").strip()
    rules = ctx.config.get("dangerous_goods", {})

    if profile in rules.get("hazardous_profiles", ["GPP"]):
        if not un or un in ("-",):
            return None, "Gefahrgut-profil is GPP but UN Nummer is empty"
        un_value = un if un.upper().startswith("UN") else f"UN{un}"
        return (
            {
                "supplier_declared_dg_hz_regulation#1.value": rules.get(
                    "hazardous_candidates", ["Transport", "Transportation"]
                ),
                "hazmat#1.aspect": rules.get(
                    "aspect_candidates",
                    ["Gefahrstoffkennung der Vereinten Nationen", "UN Regulatory Id"],
                ),
                "hazmat#1.value": un_value,
            },
            f"Gefahrgut-profil {profile}, UN {un}",
        )

    if profile and profile not in rules.get("non_hazardous_profiles", ["NOG"]):
        return None, f"unknown Gefahrgut-profil {profile!r} - not GPP and not NOG"

    return (
        {
            "supplier_declared_dg_hz_regulation#1.value": rules.get(
                "non_hazardous_candidates", ["Nicht zutreffend", "Not Applicable"]
            ),
            "hazmat#1.aspect": None,
            "hazmat#1.value": None,
        },
        f"Gefahrgut-profil {profile or 'empty'}",
    )


# ------------------------------------------------------- measures and packaging


@derivation("net_content")
def d_net_content(ctx):
    """unit_count + its type, and the liquid block when the unit is a volume."""
    amount = T.to_number(ctx.record.get("net_content"))
    if amount is None:
        return None, "Netto-füll-menge is empty or not numeric"
    raw_unit = ctx.record.get("net_content_unit")
    unit_key = str(raw_unit).strip().lower() if raw_unit else None
    units = ctx.config.get("content_units", {})
    entry = units.get(unit_key)
    if entry is None:
        return None, f"Einheit Netto-füll-menge {raw_unit!r} is not in the unit table"

    out = {
        "unit_count#1.value": amount,
        "unit_count#1.type.value": entry["unit_count_type"],
    }
    if entry.get("is_liquid"):
        out["contains_liquid_contents#1.value"] = ctx.config["yes"]
        out["liquid_volume#1.value"] = amount
        out["liquid_volume#1.unit"] = entry["liquid_unit"]
    else:
        out["contains_liquid_contents#1.value"] = ctx.config["no"]
    return out, f"Netto-füll-menge {amount} {raw_unit}"


@derivation("package_weight")
def d_package_weight(ctx):
    """Weight plus its unit, read out of the source string.

    The BDF column is headed '... KG' but carries values like '122 g' and
    '0,12 kg', so the unit is parsed from the text and only a bare number falls
    back to kilograms.
    """
    raw = ctx.record.get("weight_gross")
    amount, unit = T.parse_measure(raw, default_unit="kg")
    if amount is None:
        return None, "Gewicht Einzelstück (brutto) is empty or unreadable"
    table = ctx.config.get("weight_units", {})
    entry = table.get(unit)
    if entry is None:
        return None, f"weight unit {unit!r} is not in the weight-unit table"
    return (
        {"item_package_weight#1.value": amount, "item_package_weight#1.unit": entry},
        f"parsed {raw!r} as {amount} {unit}",
    )


@derivation("package_dimensions")
def d_package_dimensions(ctx):
    out, missing = {}, []
    unit = ctx.config["dimension_unit"]
    for src, code in (
        ("length", "item_package_dimensions#1.length"),
        ("width", "item_package_dimensions#1.width"),
        ("height", "item_package_dimensions#1.height"),
    ):
        v = T.to_number(ctx.record.get(src))
        if v is None:
            missing.append(src)
            continue
        out[f"{code}.value"] = v
        out[f"{code}.unit"] = unit
    if missing:
        return None, "missing single-unit dimension(s): " + ", ".join(missing)
    return out, "Einzelstück dimensions, millimetres"


@derivation("master_pack")
def d_master_pack(ctx):
    """Case-level dimensions and weight. Only written when the template has them."""
    unit_len = ctx.config["dimension_unit"]
    unit_wt = ctx.config["weight_units"]["kg"]
    out = {}
    for src, code in (
        ("case_length", "rtip_master_pack_dimensions#1.length"),
        ("case_width", "rtip_master_pack_dimensions#1.width"),
        ("case_height", "rtip_master_pack_dimensions#1.height"),
    ):
        v = T.to_number(ctx.record.get(src))
        if v is None:
            return None, f"missing case dimension {src}"
        out[f"{code}.value"] = v
        out[f"{code}.unit"] = unit_len
    w, wu = T.parse_measure(ctx.record.get("case_weight"), default_unit="kg")
    if w is None:
        return None, "missing Gewicht VE KG"
    if wu != "kg":
        return None, f"case weight unit {wu!r} is not kilograms"
    out["rtip_master_pack_weight#1.value"] = w
    out["rtip_master_pack_weight#1.unit"] = unit_wt
    return out, "VE dimensions and weight"


@derivation("generic_keyword")
def d_generic_keyword(ctx):
    """A German search term. The AVS guide says to pick something close to the
    chosen browse node, so that is the default; product types with a shorter
    house term override it in their overlay."""
    fixed = ctx.config.get("generic_keyword")
    if fixed:
        return fixed, "configured for this product type"
    node = ctx.resolved_value("recommended_browse_nodes#1.value")
    if node:
        return node, "mirrors the recommended browse node"
    return None, "no browse node resolved to base a keyword on"


@derivation("subcategory_override")
def d_subcategory_override(ctx):
    """Refine the MGR-derived subcategory from a title keyword where the MGR is
    too coarse (hair styling splits gels and waxes out of the main node)."""
    subcat = ctx.resolved_value("product_subcategory#1.value")
    rules = ctx.config.get("subcategory_overrides", {}).get(subcat)
    if not rules:
        return subcat, "unchanged"
    cands, kw = _keyword_pick(ctx.record.get("title"), rules, [subcat])
    if kw:
        return cands, f"refined by title keyword {kw!r}"
    return subcat, "unchanged"
