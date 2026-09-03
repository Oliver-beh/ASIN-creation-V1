# Deploying BDF VC Upload to Cloud Run (parked)

> **Not the current path.** This needs a GCP billing account, which we cannot
> raise right now. Testing runs on Streamlit Community Cloud instead --
> see `DEPLOY_STREAMLIT.md`. Keep this file: it is the runbook for moving back
> once billing exists, and `app.py` still reads the IAP identity header, so the
> move is a redeploy and a config flip, not a rewrite.

Everything here is maintainer-only. Colleagues just open the URL.

Architecture and rationale live in `WEBAPP_PLAN.md`. This file is the runbook.

---

## 0. One-time: install and authenticate gcloud

Not currently installed on this machine.

```bash
brew install --cask google-cloud-sdk
```

Then:

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
gcloud config set run/region europe-west3
```

You need **Owner**, or the set: Cloud Run Admin + IAP Policy Admin + Project IAM
Admin + Service Account User.

---

## 1. One-time: enable the APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iap.googleapis.com
```

`gcloud run deploy --source .` needs Cloud Build (to build the image) and
Artifact Registry (to store it). Both are in the free tier at this volume.

---

## 2. One-time: OAuth consent screen = Internal

Console → **APIs & Services → OAuth consent screen**.

- User type: **Internal**
- App name: `BDF VC Upload`
- Support email: your Workspace address

**Internal** is what restricts sign-in to the Remazing Workspace org. If the
console only offers **External**, the project is not attached to the Workspace
org — stop and fix that first, or IAP will let any Google account reach the
consent step.

---

## 3. Deploy

```bash
./deploy.sh
```

The script runs the cell-by-cell regression check first, asks you to confirm the
match rate, then deploys with the flags from the plan (`--session-affinity`,
`--memory 1Gi`, `--min-instances 0`, `--timeout 300`, `--no-allow-unauthenticated`)
and stamps the footer with a build id.

The regression check needs the eight verified fixture files. Point it at them:

```bash
BDF_FIXTURES=~/path/to/fixtures ./deploy.sh
```

It expects, in one folder:

| Kind | Files |
|---|---|
| Blank templates | `Deodorants_2026-08-11T15_21_00Z.xlsm`, `Haarstyling-Mittel_2026-08-11T15_22_00Z.xlsm`, `Feuchtigkeitscreme_2026-07-24T08_39_00Z.xlsm`, `Hautpflegemittel_2026-08-11T15_20_00Z.xlsm` |
| Listungen | `Listungen_20260407.xlsx`, `Listungen-Faceplus_20260722.xlsx`, `Listungen-GP-20260526_Daten_erga_nzt_Feel_Good_Set_10-07-2026.xlsx` |
| Verified human uploads | `Deodorants_2026-04-09T10_51_00Z__2_.xlsm`, `Hair_Styling_Agents_2026-04-09T10_48_00Z__1_.xlsm`, `Hautpflegemittel_2026-07-10T10_09_00Z_Feel_Good_Set.xlsm`, `Hautpflegemittel_2026-05-27T09_36_00Z_incl_cost_price.xlsm` |

Exact names — `tools/regress.py` looks them up by filename. Keep the folder out
of the repo and out of the image (`.dockerignore` already excludes the
Colab-era `templates/`, `listungen/`, `output/`).

First deploy takes 3–5 minutes (image build). Later ones are faster.

At this point the URL returns **403 for everyone, including you.** That is
correct — nothing can reach it until IAP is wired up.

---

## 4. One-time: turn on IAP and grant the domain

This is the step whose console flow Google keeps moving, so **read the console,
not just these commands.** The shape of it:

```bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# 1. Let IAP invoke the service on a user's behalf.
gcloud run services add-iam-policy-binding bdf-vc-upload \
  --region europe-west3 \
  --member "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \
  --role roles/run.invoker

# 2. Enable IAP on the service.
gcloud beta run services update bdf-vc-upload \
  --region europe-west3 \
  --iap

# 3. The one binding that means "everyone at Remazing, nobody else".
gcloud beta iap web add-iam-policy-binding \
  --resource-type=cloud-run \
  --service=bdf-vc-upload \
  --region=europe-west3 \
  --member="domain:remazing.eu" \
  --role="roles/iap.httpsResourceAccessor"
```

If `--iap` is not recognised, do it in the console: **Cloud Run → the service →
Security → Authentication → enable Cloud IAP**, then add the principal
`domain:remazing.eu` with role **IAP-secured Web App User**.

IAP can take a couple of minutes to propagate. A 403 immediately after step 3 is
usually just that.

---

## 5. Acceptance checks

Walk these before telling colleagues the URL.

- [ ] `@remazing.eu` account reaches the app.
- [ ] A personal Gmail is refused **by Google**, before the app loads.
- [ ] The sidebar shows the signed-in address (proves the
      `X-Goog-Authenticated-User-Email` header is arriving — it reads
      "unknown (not behind IAP)" if it is not).
- [ ] Footer build id matches what you just deployed.
- [ ] A known Listungen + template reproduces the Colab output. Diff the filled
      `.xlsm` and the QA `.xlsx` against a Colab run of the same inputs.
- [ ] **Open the downloaded `.xlsm` in Excel and click into
      `Produktunterkategorie`.** The cascading dropdown must still work. This is
      the `bdfvc/xlsm.py` splice, and it is the one thing that cannot be checked
      without a real blank Vendor Central template — see "Known gap" below.
- [ ] A multi-product-type Listungen: the picker's row counts match, and the
      chosen type writes the right subset.
- [ ] Cold start (leave it 20 minutes, then load) completes in roughly 3–8s.

---

## 6. Routine operations

**Ship a rule change** — edit the YAML in `config/`, then:

```bash
BDF_FIXTURES=~/path/to/fixtures ./deploy.sh
```

The next person to open the URL is on the new version. No distribution, no drift.

**Roll back:**

```bash
gcloud run revisions list --service bdf-vc-upload --region europe-west3
gcloud run services update-traffic bdf-vc-upload \
  --region europe-west3 --to-revisions <REVISION>=100
```

**Read the audit log** — one line per run, no file contents:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=bdf-vc-upload AND textPayload:"run user="' \
  --limit 40 --format 'value(timestamp, textPayload)'
```

**Add somebody outside the domain** (contractor, say) — grant them individually
rather than widening the domain binding:

```bash
gcloud beta iap web add-iam-policy-binding \
  --resource-type=cloud-run --service=bdf-vc-upload --region=europe-west3 \
  --member="user:someone@example.com" --role="roles/iap.httpsResourceAccessor"
```

**Always warm** (removes the 3–8s cold start, a few € a month):

```bash
MIN_INSTANCES=1 ./deploy.sh
```

---

## 7. Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
BUILD_ID=local .venv/bin/streamlit run app.py
```

The sidebar will read "unknown (not behind IAP)" — expected off Cloud Run.

---

## Known gap: the cascading-dropdown check

`bdfvc/xlsm.py` splices the `x14` validation block back into the saved file,
because openpyxl drops it. That path could not be exercised here: the only
`.xlsm` in the repo is `sample_output/BODY_DEODORANT__FILLED.xlsm`, which is
itself a tool output and contains **no** `x14` block and **no** `vbaProject.bin`.
With nothing to splice, `save_preserving_extensions()` correctly returns early —
so the splice is neither proven nor disproven by anything in this repo.

Two things to check on the first real run, with a blank template freshly
downloaded from Vendor Central:

1. Open the downloaded `.xlsm` in Excel and confirm the conditional dropdowns
   (`product_subcategory`, `recommended_browse_nodes`, unit-count type, EPR
   packaging) still cascade.
2. Confirm whether the blank template carries `vbaProject.bin`, and whether the
   output still has it. The sample output does not have one. If the blank
   template does, macros are being dropped somewhere and that is worth knowing —
   Amazon's ingestion reads cell values and ignores both macros and validation
   rules, so uploads still work either way, but the reviewer's experience
   changes.

Nothing to fix pre-emptively. Just do not tick the acceptance box on trust.
