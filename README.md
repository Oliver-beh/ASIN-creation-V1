# BDF DE — New ASIN Upload Automation

Turns a Beiersdorf "Listungen" input sheet into an upload-ready Amazon Vendor Central
bulk template, plus a QA report that says where every value came from.

Prototype scope: **amazon.de only** (`de_DE`). Seven product types are configured.

**Not a developer?** Open `BDF_ASIN_Upload_Colab.ipynb` in Google Colab and follow
`COLAB_SETUP.md`. No install, no terminal.

## Command line

```bash
pip install -r requirements.txt

python fill_bdf.py Deodorants_2026-08-11.xlsm \
    --input Listungen_20260407.xlsx \
    --filter-template "Body Deodorant" \
    --out-dir output/
```

Produces `<template>__FILLED.xlsm` and `<template>__QA.xlsx`, and exits `0` on
UPLOAD READY, `1` on NOT UPLOAD READY, `2` on a hard input error.

---

## 1. How well does it work

Every one of the six verified human uploads was regenerated from its own source
sheet and diffed cell by cell (`python tools/regress.py`).

| Batch | Products | Cells matched | Blockers |
|---|---|---|---|
| Deodorants | 30 | 1491 / 1496 — 99.7% | 0 |
| Hair styling | 18 | 840 / 860 — 97.7% | 0 |
| Face care (Faceplus) | 4 | 181 / 184 — 98.4% | 0 |
| Feel Good Set (July rebuild) | 1 | 51 / 53 — 96.2% | 0 |
| Gift sets (May batch) | 11 | 563 / 595 — 94.6% | 0 |
| **Total** | **64** | **3126 / 3188 — 98.1%** | **0** |

Four of the six were built on English templates, so the harness maps each English
enum to its German counterpart before comparing. Everything left is listed in
section 5 — and most of it is the automation being right where the human was not.

## 2. Architecture

```
Listungen .xlsx ─► inputsheet.py ─► canonical record ─┐
                                                      ├─► mapper.py ─► template.py ─► FILLED .xlsm
config/*.yaml + derivations.py ───────────────────────┘        │
                                                               └─► qa.py ─► QA .xlsx
```

| Module | Responsibility |
|---|---|
| `bdfvc/template.py` | Reads the Amazon template. Field codes, requirement levels, static dropdowns, cascading dropdowns, validation, writing. Knows nothing about Beiersdorf. |
| `bdfvc/inputsheet.py` | Finds the header row, matches columns by name, refuses to run if a required source column is missing. |
| `bdfvc/transforms.py` | Pure functions: GTIN padding, GS1 check digit, measure parsing, title cleaning, brand resolution. |
| `bdfvc/derivations.py` | The named rules config points at: category chain, dangerous goods, net content, packaging, scent, item form. |
| `bdfvc/mapper.py` | Resolves each field, validates it against the template, records provenance. |
| `bdfvc/qa.py` | Blockers, review items, verdict, provenance workbook. |
| `bdfvc/xlsm.py` | Saves without losing the cascading dropdowns. |
| `config/` | Every business rule. Adding a product type means adding a YAML file, not editing Python. |

| Folder | Use |
|---|---|
| `templates/` | Blank Vendor Central `.xlsm` files, one per product type |
| `listungen/` | BDF input sheets |
| `output/` | Filled templates and QA reports |

**No AI at runtime and none at build time.** Every value is a constant, a copied
source value, a table lookup, or a rule — and every one of them is checked against
the template's own dropdown before it is written. A value the template does not
offer is never written, and never approximated.

## 3. Four things worth knowing about these files

1. **The `.xlsm` has no macros.** No `vbaProject.bin` in any of the twelve
   templates. It is an `.xlsm` by extension only.
2. **openpyxl drops the cascading dropdowns.** They live in an `x14` extension
   block it does not understand. `xlsm.py` splices the block back in after saving
   and re-declares the XML namespaces openpyxl omits from the worksheet root —
   without that the file will not reopen. Verified: 17 of 17 preserved on the
   moisturiser template.
3. **The cascades resolve in pure Python.** Amazon's formula is always
   `VLOOKUP` the parent value into `'Dropdown Lists'!A:B`, then `INDIRECT` into a
   defined name. `template.py` parses that shape directly, so
   `Körperpflege` → `Face` → `Tagespflege` resolves without Excel.
4. **The brand list contains `NIVEA` and `Nivea`.** Also `NIVEA MEN`, `Nivea Men`
   and `NIVEA Men`. Matching is exact-case first, because the AVS guide requires
   all capitals and a case-insensitive match silently picks whichever appears
   first in the list.

## 4. What is verified, and what is judgement

**Deterministic, verified against the sample data:**

- Dangerous goods — `GPP` → `Transport` + UN id, `NOG` → `Nicht zutreffend`. 30/30.
- Category and subcategory from `MGR Bezeichnung`. 74/74.
- Brand from the first token of the item name. 58/58 in the April batch.
- Item name copied verbatim, minus BDF's ` 1 ST` suffix and a duplicated leading
  brand token.
- Weight parsed out of the source string: `"122 g"` → 122 Gramm, `"0,12 kg"` →
  0.12 Kilogramm. The column is headed "KG" but does not always contain kilograms.
- GTIN padded to 14 digits, with the GS1 check digit verified and reported.

**Rule-based but arguable:**

- `recommended_browse_nodes` — a title keyword within the subcategory
  (Spray → Haarsprays, Nachtpflege → Nachtpflege).
- `item_form` — a title keyword, with `Zerstäuber` and `Roll-on` deliberately
  matched before `Spray`. A pressurised product with no form word in its name
  falls back to Aerosol.

**Flagged for a human every time:**

- `scent`. The verified uploads are internally inconsistent — identical
  Hidrofugal Classic products were given both `Unscented` and `Fresh` — so no
  rule can reproduce past practice. The engine is deterministic and asks for
  confirmation rather than pretending.

## 5. Where the output differs from the human uploads

Every remaining difference, and why:

| Difference | Rows | Verdict |
|---|---|---|
| `cost_price` empty | 12 | Correct. The gift-set source sheet has no cost prices; QA flags each one. The human added them later by hand. |
| `liquid_volume` = net content, human used less | 18 | We follow the AVS guide, which says Liquid Volume is the Netto-füll-menge. The human used a propellant-free figure that appears in no source column. |
| `target_audience_keyword` follows the source | 11 | Source says `Unisex`, human wrote `Damen` from product knowledge. We stay faithful to the source; QA shows the value and where it came from. |
| `item_name` keeps ` 1 ST` in the May batch | 11 | We match the **July** rebuild of the same products, which stripped it. May was the older practice. |
| `scent` on Hidrofugal Classic | 5 | Human was inconsistent across the same product family. Ours is self-consistent and always flagged. |
| `skin_type` = `Alle`, human wrote `Normal` | 3 | Source says "Alle Hauttypen" and the template offers `Alle`. The automation is more accurate here. |
| `brand` = NIVEA, human wrote NIVEA MEN | 2 | Source `Zielgruppe` says `Frauen`/`Unisex` for products that are in fact NIVEA MEN. A source-data error nothing can derive around. |

Two of these are worth raising with BDF: the wrong `Zielgruppe` on two hair and
gift SKUs, and the missing cost prices on the gift-set sheet.

## 6. What it does not do yet

- **GPSR and compliance columns are left empty.** They exist in the templates
  (`gpsr_safety_attestation`, `gpsr_manufacturer_reference`,
  `dsa_responsible_party_address`, `compliance_media`) and QA flags them on every
  run. Filling them at creation time is the single highest-value addition, and it
  needs three facts from Beiersdorf: the EU Responsible Person contact, the
  manufacturer contact, and the compliance-document URL pattern. They are
  deliberately not invented.
- **No variation handling.** No parent/child rows appear anywhere in the sample
  data. If variation families arrive, this needs designing, not patching.
- **`de_DE` only.** The English templates use a different value vocabulary
  (Gramm/Grams, Frisch/Fresh, Einheit/Unit). Supporting GB means a second
  vocabulary block in config, not a translation layer.
- **Product-type routing is manual.** Use `--filter-template` when one input
  sheet covers several templates.

## 7. Adding a product type

1. Download the blank template for it from Vendor Central, in German.
2. Create `config/product_types/<PRODUCT_TYPE>.yaml` — usually four lines.
3. Add any new `MGR Bezeichnung` to `mgr_categories` in `config/common.yaml`.
4. Add its subcategory to `browse_nodes`.
5. Run against a real batch and read the QA report.

Nothing else should need touching. If it does, that is a sign a business rule
leaked into the engine.
