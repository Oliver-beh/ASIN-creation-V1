"""Streamlit front end for the BDF Vendor Central template filler.

This is a thin wrapper. It uploads two files to a temp dir, calls the unmodified
`build()` from fill_bdf.py, renders the returned QAReport, and hands back the two
output files as downloads. Nothing is persisted: the temp dir dies with the run
and the bytes live in Streamlit's session state only.

The engine (bdfvc/) and the rules (config/) are deliberately untouched — see
WEBAPP_PLAN.md §6.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bdfvc.inputsheet import InputSheetError, ListungenSheet  # noqa: E402
from bdfvc.template import VCTemplate  # noqa: E402
from fill_bdf import build  # noqa: E402

BUILD_ID = os.environ.get("BUILD_ID", "dev")
BUILD_DATE = os.environ.get("BUILD_DATE", "unbuilt")
CONFIG_DIR = HERE / "config"

MAIN_BLUE = "#002F40"
TEAL = "#1CA0A5"
HIGHLIGHT = "#F95954"

log = logging.getLogger("bdf_vc_upload")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


# --------------------------------------------------------------------- helpers

def _norm(s) -> str:
    """Loose match key: 'BODY_DEODORANT' and 'Body Deodorant' collapse to the same."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def authenticated_email() -> str:
    """Who the host says is viewing, or a placeholder when nothing asserts an identity.

    Two hosts, two mechanisms, tried in this order:

    1. **Streamlit Community Cloud** fills ``st.user["email"]`` with the
       viewer's address -- but *only when the app is set to private*. A public
       app, or a local ``streamlit run``, leaves it empty. That emptiness is the
       tell: if the sidebar reads "unknown" on the deployed URL, the app is not
       private, the viewer allowlist is not being enforced, and anyone with the
       link is inside. Treat it as a stop sign, not a cosmetic bug.
    2. **Cloud Run behind IAP** sets X-Goog-Authenticated-User-Email to
       'accounts.google.com:someone@x.eu'. Kept working so the GCP path is a
       redeploy away -- see DEPLOY_CLOUDRUN.md.
    """
    try:
        email = st.user.get("email")
    except Exception:  # no script-run context, or no identity provider wired up
        email = None
    if email:
        return str(email)

    try:
        headers = st.context.headers or {}
    except Exception:  # older Streamlit, or no request context
        headers = {}
    raw = headers.get("X-Goog-Authenticated-User-Email") or headers.get(
        "x-goog-authenticated-user-email"
    )
    if raw:
        return raw.split(":", 1)[-1]

    return "unknown (app not private / local run)"


@st.cache_data(show_spinner=False, max_entries=4)
def scan_listungen(data: bytes, filename: str):
    """Read the Listungen once to offer the Template picker.

    Returns (sheet_name, header_row, [(template_value, row_count)], total_rows).
    Cached on the uploaded bytes so re-renders don't re-parse the workbook.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (filename or "listungen.xlsx")
        p.write_bytes(data)
        sheet = ListungenSheet(p)
        counts: dict[str, int] = {}
        total = 0
        for _row, rec in sheet.rows():
            total += 1
            hint = str(rec.get("template_hint") or "").strip()
            counts[hint or "(blank)"] = counts.get(hint or "(blank)", 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return sheet.sheet_name, sheet.header_row, ordered, total


@st.cache_data(show_spinner=False, max_entries=4)
def scan_template(data: bytes, filename: str):
    """Read the template once to show its product type / locale. Returns (pt, locale)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (filename or "template.xlsm")
        p.write_bytes(data)
        t = VCTemplate(p)
        return t.product_type, t.locale


def run_build(listungen_bytes, listungen_name, template_bytes, template_name, filter_template):
    """Write both uploads to a temp dir, call build() unchanged, read the outputs back."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        in_path = td / (listungen_name or "listungen.xlsx")
        tpl_path = td / (template_name or "template.xlsm")
        in_path.write_bytes(listungen_bytes)
        tpl_path.write_bytes(template_bytes)
        out_dir = td / "out"

        report, out_xlsm, out_qa = build(
            tpl_path,
            in_path,
            out_dir,
            config_dir=CONFIG_DIR,
            sheet=None,
            filter_template=filter_template,
        )
        return {
            "report": report,
            "filter": filter_template,
            "xlsm_name": Path(out_xlsm).name,
            "xlsm_bytes": Path(out_xlsm).read_bytes(),
            "qa_name": Path(out_qa).name,
            "qa_bytes": Path(out_qa).read_bytes(),
        }


def provenance_rows(report):
    """Flatten the report into the field-provenance table, mirroring qa.py's sheet."""
    out = []
    for row in report.rows:
        for f in row.fields.values():
            if f.status == "absent":
                continue
            out.append(
                {
                    "Row": row.target_row,
                    "NART": row.nart,
                    "Field code": f.code,
                    "Column label": f.label,
                    "Requirement": f.requirement,
                    "Value": "" if f.value is None else str(f.value),
                    "Source": f.provenance,
                    "Status": f.status,
                    "Note": f.note,
                }
            )
    return out


# ------------------------------------------------------------------------- page

st.set_page_config(page_title="BDF VC Upload", page_icon="📄", layout="wide")

st.markdown(
    f"""
    <style>
      .stAppHeader {{ background: transparent; }}
      h1 {{ color: {MAIN_BLUE}; }}
      .verdict {{ padding: 0.9rem 1.2rem; border-radius: 6px; color: #fff;
                  font-weight: 700; font-size: 1.4rem; letter-spacing: .02em; }}
      .verdict-ok  {{ background: {TEAL}; }}
      .verdict-bad {{ background: {HIGHLIGHT}; }}
      .foot {{ color: #7a8b91; font-size: 0.8rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("BDF Vendor Central Upload")
st.caption(
    "Upload the BDF Listungen and a **fresh** Vendor Central template. "
    "You get back an upload-ready `.xlsm` and a QA report. "
    "Nothing is stored — files live in memory for this run only."
)

user_email = authenticated_email()
with st.sidebar:
    st.markdown("**Signed in as**")
    st.code(user_email, language=None)
    st.markdown("---")
    st.markdown("**How it works**")
    st.markdown(
        "1. Upload the Listungen `.xlsx`\n"
        "2. Upload a fresh template `.xlsm` from Vendor Central\n"
        "3. Pick the product type to run\n"
        "4. Generate, read the QA, download"
    )
    st.markdown("---")
    st.markdown(
        "Always download a **fresh** template per run. Columns are matched by "
        "header title, so a re-formatted template still works — but a stale one "
        "may be missing columns Amazon has since added."
    )
    st.markdown("---")
    st.markdown(
        "Per Remazing's internal AI guidelines: keep personal data (names, "
        "emails, contact details) out of these sheets and out of any AI tool — "
        "product and template data only."
    )

col_a, col_b = st.columns(2)
with col_a:
    listungen_file = st.file_uploader("BDF Listungen (.xlsx)", type=["xlsx", "xlsm"], key="listungen")
with col_b:
    template_file = st.file_uploader("Fresh VC template (.xlsm)", type=["xlsm"], key="template")

listungen_bytes = listungen_file.getvalue() if listungen_file else None
template_bytes = template_file.getvalue() if template_file else None

# ------------------------------------------------------- inspect both uploads

template_pt = template_locale = None
if template_bytes:
    try:
        template_pt, template_locale = scan_template(template_bytes, template_file.name)
    except Exception as e:
        st.error(f"Could not read that template: {type(e).__name__}: {e}")
        template_bytes = None
    else:
        msg = f"Template product type: **{template_pt}** · locale `{template_locale}`"
        if not str(template_locale).startswith("de"):
            st.warning(msg + " — this tool is configured for the German marketplace (`de_DE`) only.")
        else:
            st.success(msg)

template_options = []
listungen_meta = None
if listungen_bytes:
    try:
        sheet_name, header_row, template_options, total_rows = scan_listungen(
            listungen_bytes, listungen_file.name
        )
    except InputSheetError as e:
        st.error(f"Input sheet problem: {e}")
        listungen_bytes = None
    except Exception as e:
        st.error(f"Could not read that Listungen: {type(e).__name__}: {e}")
        listungen_bytes = None
    else:
        listungen_meta = (sheet_name, header_row, total_rows)
        st.info(
            f"Listungen sheet **{sheet_name}**, header on row {header_row}, "
            f"{total_rows} product row(s)."
        )

# ------------------------------------------------------- product-type picker

filter_template = None
if template_options:
    labels = [f"{name} — {n} row(s)" for name, n in template_options]
    label_to_value = {
        lbl: (None if name == "(blank)" else name)
        for lbl, (name, _n) in zip(labels, template_options)
    }
    all_label = f"All rows — {sum(n for _t, n in template_options)} row(s)"
    labels = labels + [all_label]
    label_to_value[all_label] = None

    default_ix = len(labels) - 1  # "All rows" unless we can match the template
    if template_pt:
        want = _norm(template_pt)
        for i, (name, _n) in enumerate(template_options):
            if name != "(blank)" and _norm(name) == want:
                default_ix = i
                break

    st.markdown("#### Which product type should be written?")
    if len(template_options) > 1:
        st.caption(
            "This Listungen covers more than one product type (its `Template` column). "
            "The uploaded template holds one. Pick the matching one — the rest are skipped."
        )
    choice = st.radio(
        "Rows to include",
        labels,
        index=default_ix,
        label_visibility="collapsed",
    )
    filter_template = label_to_value[choice]

    if template_pt and filter_template and _norm(filter_template) != _norm(template_pt):
        st.warning(
            f"You picked **{filter_template}** but the template is for **{template_pt}**. "
            "That is usually a mistake — check before you upload the result to Vendor Central."
        )

# ------------------------------------------------------------------- the run

ready = bool(listungen_bytes and template_bytes)
if not ready:
    st.button("Generate upload file", disabled=True, type="primary")
    st.caption("Upload both files to enable this.")
else:
    if st.button("Generate upload file", type="primary"):
        with st.spinner("Filling the template…"):
            try:
                st.session_state["result"] = run_build(
                    listungen_bytes,
                    listungen_file.name,
                    template_bytes,
                    template_file.name,
                    filter_template,
                )
            except InputSheetError as e:
                st.session_state.pop("result", None)
                st.error(f"Input sheet problem: {e}")
            except Exception as e:
                st.session_state.pop("result", None)
                st.error(f"Failed: {type(e).__name__}: {e}")
                log.exception("build failed for %s", user_email)
            else:
                r = st.session_state["result"]["report"]
                # Audit trail: Cloud Logging picks this up from stdout. No file contents,
                # no personal data beyond the already-authenticated identity.
                log.info(
                    "run user=%s listungen=%s template=%s product_type=%s "
                    "filter=%s rows=%d blockers=%d verdict=%s build=%s",
                    user_email,
                    listungen_file.name,
                    template_file.name,
                    r.template.product_type,
                    filter_template,
                    len(r.rows),
                    len(r.blockers()),
                    r.status,
                    BUILD_ID,
                )

# --------------------------------------------------------------- result view

def render_result(result):
    """Render the QAReport in the page. Kept a function so it can be exercised
    without driving a file upload through a browser."""
    report = result["report"]
    blockers = report.blockers()
    warnings_ = report.warnings()
    ok = report.status == "UPLOAD READY"

    st.markdown("---")
    st.markdown(
        f'<div class="verdict {"verdict-ok" if ok else "verdict-bad"}">{report.status}</div>',
        unsafe_allow_html=True,
    )

    # Product type goes in a caption, not a metric — a metric box truncates it.
    st.caption(
        f"**{report.template.product_type}** · `{report.template.locale}`"
        + (f" · rows filtered to *{result['filter']}*" if result.get("filter") else " · all rows")
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Products written", len(report.rows))
    m2.metric("Blocking issues", len(blockers))
    m3.metric("Items to review", len(warnings_))

    d1, d2 = st.columns(2)
    d1.download_button(
        "⬇ Filled template (.xlsm)",
        data=result["xlsm_bytes"],
        file_name=result["xlsm_name"],
        mime="application/vnd.ms-excel.sheet.macroEnabled.12",
        type="primary",
        use_container_width=True,
    )
    d2.download_button(
        "⬇ QA report (.xlsx)",
        data=result["qa_bytes"],
        file_name=result["qa_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    if not ok:
        st.error(
            "Do not upload this file to Vendor Central. Fix the blockers in the "
            "Listungen and run again."
        )

    st.markdown("### Blocking issues")
    if blockers:
        st.caption("Each of these stops the upload. Fix the source data and re-run.")
        for b in blockers:
            st.markdown(f"- {b}")
    else:
        st.success(
            "None. Every required column is populated and every value is a valid "
            "template option."
        )

    # report.warnings() already folds in report.notes (see qa.py), which is also
    # what the QA workbook's Review sheet shows. Don't render notes twice.
    st.markdown("### Needs a human eye (not blocking)")
    if warnings_:
        st.caption(
            "Scent (`Duft`) is flagged on every row by design — the rule cannot "
            "verify it, so a human confirms it. Empty cost prices and GPSR gaps "
            "show up here too."
        )
        with st.expander(f"{len(warnings_)} item(s)", expanded=len(warnings_) <= 12):
            for w in warnings_:
                st.markdown(f"- {w}")
    else:
        st.success("None.")

    st.markdown("### Where every value came from")
    counts = report.summary_counts()
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Fixed value", counts.get("const", 0))
    p2.metric("Copied from source", counts.get("source", 0))
    p3.metric("Lookup table", counts.get("lookup", 0))
    p4.metric("Rule", counts.get("derived", 0))
    st.caption(
        "Every cell written, and why. Nothing is guessed: a value the rules cannot "
        "resolve, or one the template's own dropdown rejects, is left empty and listed above."
    )
    st.dataframe(provenance_rows(report), use_container_width=True, height=420)


result = st.session_state.get("result")
if result:
    render_result(result)


st.markdown("---")
st.markdown(
    f'<div class="foot">Build <code>{BUILD_ID}</code> · deployed {BUILD_DATE} · '
    f"de_DE only · stateless, nothing uploaded here is stored</div>",
    unsafe_allow_html=True,
)
