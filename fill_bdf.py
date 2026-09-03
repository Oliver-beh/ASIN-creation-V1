#!/usr/bin/env python3
"""Beiersdorf DE - new-ASIN Vendor Central template filler.

    python fill_bdf.py TEMPLATE.xlsm --input Listungen.xlsx --out-dir out/

Reads a blank (or prefilled) Amazon Vendor Central bulk template and a BDF
"Listungen" input sheet, writes one Amazon row per source product, and produces
an upload-ready .xlsm plus a QA workbook.

Nothing is guessed. Any value the rules cannot resolve, and any value the
template's own dropdown rejects, is left empty and reported.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bdfvc import config as cfgmod
from bdfvc.inputsheet import InputSheetError, ListungenSheet
from bdfvc.mapper import Mapper
from bdfvc.qa import QAReport
from bdfvc.template import FIRST_DATA_ROW, VCTemplate

HERE = Path(__file__).resolve().parent


def build(template_path, input_path, out_dir, config_dir=None, sheet=None, filter_template=None):
    config_dir = Path(config_dir or HERE / "config")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    template = VCTemplate(template_path)
    if not template.locale.startswith("de"):
        print(
            f"  ! Template locale is {template.locale}, not de_DE. "
            f"This prototype is configured for the German marketplace only.",
            file=sys.stderr,
        )

    cfg = cfgmod.load(config_dir, template.product_type)
    sheet_obj = ListungenSheet(input_path, sheet=sheet)

    report = QAReport(template, cfg, input_path, template_path)
    if cfg.get("_overlay") is None:
        report.notes.append(
            f"No product-type overlay for {template.product_type}; only the shared "
            f"configuration was applied. Available: {', '.join(cfgmod.available_product_types(config_dir))}"
        )
    if sheet_obj.unmapped:
        report.notes.append(
            f"{len(sheet_obj.unmapped)} input column(s) were not recognised and were ignored: "
            + ", ".join(sheet_obj.unmapped[:15])
            + ("..." if len(sheet_obj.unmapped) > 15 else "")
        )

    template.clear_data_rows()
    mapper = Mapper(template, cfg)

    target = FIRST_DATA_ROW
    skipped = 0
    for source_row, record in sheet_obj.rows():
        if filter_template:
            hint = str(record.get("template_hint") or "").strip().casefold()
            if hint and hint != filter_template.strip().casefold():
                skipped += 1
                continue
        report.add_row(mapper.map_row(record, source_row, target))
        target += 1

    if skipped:
        report.notes.append(f"{skipped} source row(s) skipped by --filter-template {filter_template!r}")

    stem = Path(template_path).stem
    out_xlsm = out_dir / f"{stem}__FILLED.xlsm"
    out_qa = out_dir / f"{stem}__QA.xlsx"
    template.save(out_xlsm)
    report.write(out_qa)

    return report, out_xlsm, out_qa


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("template", help="Vendor Central .xlsm downloaded for one product type")
    p.add_argument("--input", required=True, help="BDF Listungen .xlsx")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--config-dir", default=None)
    p.add_argument("--sheet", default=None, help="input worksheet name (auto-detected by default)")
    p.add_argument(
        "--filter-template",
        default=None,
        help="only take source rows whose 'Template' column matches, e.g. 'Body Deodorant'",
    )
    args = p.parse_args(argv)

    try:
        report, out_xlsm, out_qa = build(
            args.template, args.input, args.out_dir, args.config_dir, args.sheet, args.filter_template
        )
    except InputSheetError as e:
        print(f"Input sheet problem: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"Failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print(f"\n  Product type : {report.template.product_type} ({report.template.locale})")
    print(f"  Products     : {len(report.rows)}")
    print(f"  Upload file  : {out_xlsm}")
    print(f"  QA report    : {out_qa}")

    blockers, warnings = report.blockers(), report.warnings()
    if blockers:
        print(f"\n  {len(blockers)} blocking issue(s):")
        for b in blockers[:12]:
            print(f"    - {b}")
        if len(blockers) > 12:
            print(f"    ... and {len(blockers) - 12} more (see QA report)")
    if warnings:
        print(f"\n  {len(warnings)} item(s) to review:")
        for w in warnings[:8]:
            print(f"    - {w}")
        if len(warnings) > 8:
            print(f"    ... and {len(warnings) - 8} more (see QA report)")

    print(f"\n  STATUS: {report.status}\n")
    return 0 if report.status == "UPLOAD READY" else 1


if __name__ == "__main__":
    sys.exit(main())
