# Running this in Google Colab

Everything you need is in this folder. No local Python install, no terminal.

## What you need

| | |
|---|---|
| A Google account | For Colab and Drive |
| A blank Vendor Central template `.xlsm` | One per product type. Download with **VC language set to German** — the sheet comes in whatever language VC is set to, and the catalogue only accepts the German spelling of `Anzahl von Einheiten – Typ`. |
| A BDF Listungen `.xlsx` | Exactly as Beiersdorf sent it. Do **not** pre-process it. |

The manual prep from the old process — padding GTINs with zeros, converting price columns from
currency to number, adding a template column next to `MGR Bezeichnung` — is all handled in code.
Doing it by hand first will not break anything, but it is wasted effort.

## Setup, once

1. Upload the whole `bdf_vc_upload` folder to your Google Drive. Easiest way: drag
   `bdf_vc_upload.zip` into Drive, then right-click → **Extract**. Keep the folder name.
2. Open `BDF_ASIN_Upload_Colab.ipynb` from Drive — right-click → **Open with → Google Colaboratory**.
   If Colab is not listed, use *Connect more apps* and add it.
3. In the notebook, run **Step 1** and **Step 2A**. Step 2A asks permission to mount your Drive;
   approve it.

That's it. From then on you only run steps 3 to 7.

## Each time you list products

1. Drop the template `.xlsm` into `bdf_vc_upload/templates/` in Drive.
2. Drop the Listungen `.xlsx` into `bdf_vc_upload/listungen/`.
3. Open the notebook, **Runtime → Run all**.
4. Set `TEMPLATE_INDEX`, `SHEET_INDEX` and `FILTER_TEMPLATE` in Step 4 from the lists printed
   in Step 3, then re-run from Step 4 down.
5. Read Step 6. Fix any blockers, confirm the review items.
6. Step 7 downloads the filled template and the QA report. They are also saved in
   `bdf_vc_upload/output/` in Drive.

## One sheet, several product types

A Listungen sheet usually covers more than one template. The `Template` column decides which rows
belong where, and `FILTER_TEMPLATE` selects them. The cell under Step 4 prints the exact values
present in your sheet — for `Listungen_20260407` that is:

```
  30  Body Deodorant
  18  Hair Styling Agent
   9  Skin Moisturiser
   1  Skin Cleaning Agent
```

Run once per product type with the matching blank template, or use **Appendix B** to loop
through all of them in one go.

## Reading the QA report

Two things matter.

**Blockers** stop the upload. There are four kinds: a required column left empty, a value the
template's dropdown does not accept, a duplicate GTIN or SKU inside the batch, and a GTIN whose
GS1 check digit fails. All four mean something upstream is wrong — usually missing source data.

**Review items** do not stop the upload. They are values a human should glance at:

- `Duft` (scent) is flagged on every row by design. The historical uploads are inconsistent here —
  identical Hidrofugal Classic products were given both `Unscented` and `Fresh` — so the tool picks
  deterministically and asks rather than guessing silently.
- Conditionally-required columns left empty, usually because the source sheet has no value.
  The gift-set sheet arriving without cost prices shows up this way.
- GPSR and compliance columns, which are not yet filled.

The **Field provenance** tab lists every cell written and where the value came from: a fixed
default, a copy from the source sheet, a lookup table, or a rule. Start there when a value looks
wrong.

## When something goes wrong

| Message | What it means |
|---|---|
| `Not found: /content/drive/MyDrive/bdf_vc_upload` | The folder is somewhere else in Drive, or was renamed. Fix the `PROJECT` path in Step 2A. |
| `no sheet looks like a Listungen sheet` | The file has no `NART` / `Artikellangtext 1` header. Wrong file, or a heavily edited sheet. |
| `input sheet is missing required column(s)` | A column the tool needs was renamed or removed. The message names it. Add a spelling to `SYNONYMS` in `bdfvc/inputsheet.py` if BDF has renamed something permanently. |
| `No product-type overlay for X` | No config file for that product type yet. It still runs on the shared config; see section 7 of the main README. |
| `Template locale is en_GB, not de_DE` | You downloaded the template with VC set to English. Switch VC to German and download again. |
| Colab says the session expired | Normal after idle. Re-run Steps 1 and 2A. Anything already written to `output/` in Drive is safe. |

## Notes

- Colab sessions are temporary. Only Drive persists. Work from Drive (Step 2A) rather than
  uploading the zip (Step 2B) if you want to keep anything.
- Do not upload files containing personal data — names, email addresses, contact details — to
  Colab. Product and template data is fine. Follow Remazing's internal AI guidelines.
- The upload sheet still goes to `e2elisting@amazon.com` with Milan on CC. This tool produces the
  attachment; it does not send anything.
