"""The mapping engine.

One pass per product row:

  1. resolve every configured field to a *candidate* value
  2. validate that candidate against the template's own dropdown for that column
  3. write it, or record why it could not be written

Fields are written in cascade order, so a dropdown that depends on another cell
(product_subcategory on product_category, unit_count type on item_form) always
sees its driver already populated.

The engine knows no business rules. Everything specific to Beiersdorf lives in
config/ and derivations.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from . import transforms as T
from .derivations import REGISTRY as DERIVATIONS
from .template import FIRST_DATA_ROW


@dataclass
class FieldResult:
    code: str
    label: str = ""
    requirement: str = ""
    value: object = None
    provenance: str = ""       # const | source | derived | lookup | option
    note: str = ""
    status: str = "ok"         # ok | review | unresolved | rejected | absent | skipped
    candidates: list = dc_field(default_factory=list)


@dataclass
class RowResult:
    source_row: int
    target_row: int
    nart: str = ""
    title: str = ""
    fields: dict = dc_field(default_factory=dict)

    def add(self, res: FieldResult):
        self.fields[res.code] = res

    def problems(self):
        return [f for f in self.fields.values() if f.status in ("unresolved", "rejected")]

    def reviews(self):
        return [f for f in self.fields.values() if f.status == "review"]


class Context:
    """Per-row resolution context handed to derivations."""

    def __init__(self, mapper, record, row_result, target_row, current_col=None):
        self.mapper = mapper
        self.record = record
        self.row = row_result
        self.target_row = target_row
        self.current_col = current_col
        self.config = mapper.config
        self.template = mapper.template
        self.low_confidence = False

    def resolved_value(self, code):
        res = self.row.fields.get(code)
        return res.value if res and res.status in ("ok", "review") else None

    def allowed_values(self):
        if self.current_col is None:
            return None
        return self.template.allowed_values(self.current_col, self.target_row)


class Mapper:
    def __init__(self, template, config):
        self.template = template
        self.config = config
        self.fields = self._ordered_fields()

    # ------------------------------------------------------------- field order

    def _ordered_fields(self):
        """Config order, then stable-sorted by cascade depth of the primary column."""
        specs = list(self.config["fields"])
        for i, spec in enumerate(specs):
            col = self.template.resolve(spec["code"])
            spec["_col"] = col
            spec["_depth"] = self.template.cascade_depth(col) if col else 0
            spec["_seq"] = i
        return sorted(specs, key=lambda s: (s["_depth"], s["_seq"]))

    # ------------------------------------------------------------------ public

    def map_row(self, record, source_row, target_row):
        row = RowResult(
            source_row=source_row,
            target_row=target_row,
            nart=str(record.get("nart") or ""),
            title=str(record.get("title") or ""),
        )
        for spec in self.fields:
            self._resolve_spec(spec, record, row, target_row)
        return row

    # ---------------------------------------------------------------- internals

    def _skip(self, spec, record, row):
        cond = spec.get("skip_when")
        if not cond:
            return False
        if cond.get("subcategory_in"):
            sub = row.fields.get("product_subcategory#1.value")
            return bool(sub and sub.value in cond["subcategory_in"])
        if cond.get("source_empty"):
            return record.get(cond["source_empty"]) in (None, "")
        return False

    def _resolve_spec(self, spec, record, row, target_row):
        code = spec["code"]
        rule = spec.get("rule", {})

        if self._skip(spec, record, row):
            row.add(FieldResult(code=code, status="skipped", note="skipped by rule"))
            return

        col = spec.get("_col")
        if col is None:
            if spec.get("required_in_template", False):
                row.add(
                    FieldResult(code=code, status="unresolved", note="field code absent from this template")
                )
            else:
                row.add(FieldResult(code=code, status="absent", note="not present in this template"))
            return

        candidates, provenance, note, low_conf = self._candidates(spec, rule, record, row, target_row, col)
        on_reject = spec.get("on_reject", "block")
        always_review = bool(spec.get("always_review"))

        if candidates is None:
            row.add(
                FieldResult(
                    code=code,
                    label=self.template.label(code),
                    requirement=self.template.requirement(code),
                    status="unresolved",
                    provenance=provenance,
                    note=note,
                )
            )
            return

        # A derivation may return a whole group of fields at once.
        if isinstance(candidates, dict):
            for sub_code, sub_val in candidates.items():
                self._write_value(sub_code, sub_val, provenance, note, row, target_row,
                                  low_conf or always_review, on_reject)
            return

        self._write_value(code, candidates, provenance, note, row, target_row,
                          low_conf or always_review, on_reject)

    def _candidates(self, spec, rule, record, row, target_row, col):
        """-> (candidate(s) | None, provenance, note, low_confidence)"""
        if "const" in rule:
            return rule["const"], "const", "fixed value", False

        if "candidates" in rule:
            return list(rule["candidates"]), "const", "first valid template option", False

        if "source" in rule:
            raw = record.get(rule["source"])
            if raw in (None, ""):
                return None, "source", f"source column {rule['source']!r} is empty", False
            tf = rule.get("transform")
            if tf:
                fn = {
                    "number": T.to_number,
                    "int": T.to_int,
                    "money": T.round_money,
                    "date": T.to_date,
                    "gtin14": T.gtin14,
                    "text": lambda v: str(v).strip(),
                }.get(tf)
                if fn is None:
                    return None, "source", f"unknown transform {tf!r}", False
                val = fn(raw)
                if val is None:
                    return None, "source", f"{rule['source']}={raw!r} failed transform {tf}", False
                return val, "source", f"{rule['source']} -> {tf}", False
            return raw, "source", rule["source"], False

        if "lookup" in rule:
            table = self.config["lookups"].get(rule["lookup"], {})
            raw = record.get(rule["source_column"])
            if raw in (None, ""):
                if "default" in rule:
                    return list(rule["default"]), "lookup", "lookup default (source empty)", True
                return None, "lookup", f"source column {rule['source_column']!r} is empty", False
            key = str(raw).strip()
            cands = table.get(key) or table.get(key.casefold())
            if cands is None:
                return None, "lookup", f"{key!r} is not in lookup table {rule['lookup']!r}", False
            return list(cands), "lookup", f"lookup {rule['lookup']}[{key}]", False

        if "derive" in rule:
            fn = DERIVATIONS.get(rule["derive"])
            if fn is None:
                return None, "derived", f"unknown derivation {rule['derive']!r}", False
            ctx = Context(self, record, row, target_row, current_col=col)
            value, note = fn(ctx)
            return value, "derived", note, ctx.low_confidence

        return None, "", "field has no rule", False

    def _write_value(self, code, value, provenance, note, row, target_row, low_conf, on_reject="block"):
        col = self.template.resolve(code)
        label = self.template.label(code)
        requirement = self.template.requirement(code)

        if col is None:
            row.add(FieldResult(code=code, status="absent", note="not present in this template"))
            return

        if value is None:
            self.template.write(target_row, col, None)
            row.add(
                FieldResult(
                    code=code, label=label, requirement=requirement,
                    value=None, provenance=provenance, note=note or "cleared", status="ok",
                )
            )
            return

        cands = value if isinstance(value, list) else [value]
        for cand in cands:
            ok = self.template.validate(col, cand, target_row)
            if ok is not None:
                self.template.write(target_row, col, ok)
                row.add(
                    FieldResult(
                        code=code, label=label, requirement=requirement,
                        value=ok, provenance=provenance, note=note,
                        status="review" if low_conf else "ok",
                        candidates=cands,
                    )
                )
                return

        allowed = self.template.allowed_values(col, target_row)
        detail = (
            f"none of {cands!r} is an allowed value"
            + (f" (template offers {len(allowed)} options)" if allowed else "")
        )
        # `on_reject: review` means "leave the cell empty and tell a human" rather
        # than "block the upload" - used where the template genuinely has no
        # suitable option and the documented manual process is to leave it blank.
        row.add(
            FieldResult(
                code=code, label=label, requirement=requirement,
                value=None, provenance=provenance,
                note=f"{note}; {detail}" if note else detail,
                status="review" if on_reject == "review" else "rejected",
                candidates=cands,
            )
        )


def first_target_row():
    return FIRST_DATA_ROW
