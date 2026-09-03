# BDF VC Upload — Web App Build Plan (handoff spec)

> **Purpose of this file.** This is a self-contained brief for a fresh chat/session to
> build and deploy the web version of this tool. It was written in a planning
> session; the person picking it up should read it top to bottom, confirm the two
> open decisions in §9, then build. **Do not modify the existing engine logic** —
> wrap it. See §6 for what is off-limits and why.

---

## 1. What this project is (context for a fresh session)

A deterministic Amazon Vendor Central template filler for Beiersdorf DE.

- **In:** a blank VC template `.xlsm` (one product type, downloaded from Vendor
  Central in German) + a BDF "Listungen" `.xlsx` (the raw product data).
- **Out:** a filled, upload-ready `.xlsm` + a QA report `.xlsx`.
- **How:** pure Python, no AI, no database. Deps: `openpyxl`, `PyYAML` only.
  Every value written is a constant, a copied source value, a table lookup, or a
  rule — and is checked against the template's own dropdown before writing.
- **Read `README.md` and `COLAB_SETUP.md`** for full behaviour. The engine lives in
  `bdfvc/`; all business rules live in `config/` (YAML). Adding a product type =
  adding a YAML file, not editing Python.
- The one clean entry point is `build()` in `fill_bdf.py`:
  `build(template_path, input_path, out_dir, config_dir=None, sheet=None,
  filter_template=None) -> (QAReport, out_xlsm_path, out_qa_path)`. **The web app
  wraps this.**

### Fragile bits to respect (do not "clean up")
- `bdfvc/xlsm.py` splices an `x14` XML block back into the saved file to preserve
  Amazon's cascading dropdowns; openpyxl drops them otherwise. Leave it exactly
  as is. Runs fine on Linux/Cloud Run (no Excel needed).
- Column matching is by **header title, not position** — that's deliberate, so a
  fresh template each run survives Amazon reformatting the sheet. This is why both
  input files are uploaded fresh every run and nothing is cached server-side.

## 2. Why we're moving it online (the actual goal)

Today every colleague keeps their **own copy** of the whole folder in Google Drive
and runs it in Colab. Problems: (a) Colab exposes code cells and numeric indices
(`TEMPLATE_INDEX`, `SHEET_INDEX`, "re-run from Step 4") that confuse non-technical
users; (b) an update means the maintainer re-distributing the folder and everyone
swapping it in — **version drift is the core pain.**

The move fixes both by making **one deployed app the single source of truth**:
- Nobody holds the code. Colleagues open a URL and use a web page.
- An update = one redeploy. The next person to open the URL is on the new version.
  No distribution, no per-person update, no drift.
- The Colab-era `templates/`, `listungen/`, `output/` folders **disappear** —
  uploads live in memory, outputs stream to download buttons, nothing is persisted.

## 3. Decided architecture (do not re-litigate)

| Concern | Decision |
|---|---|
| UI | **Streamlit**, wrapping the existing `build()`. ~150 lines, no engine changes. |
| Host | **Google Cloud Run**, region **europe-west3** (Frankfurt, EU/German data), scale-to-zero. |
| Auth | **Google Workspace via IAP**, IAM grant to `domain:remazing.eu`. Zero auth code. (App-level Google OIDC is the documented fallback — see §5.) |
| Source / deploy | Stay entirely on GCP. Deploy with `gcloud run deploy --source .` from the maintainer's laptop. Keep a **local Git repo** for history (Git ≠ GitHub; nothing is pushed to any external host). Cloud Source Repositories is closed to new customers — do not use it. |
| State | **None.** Stateless. No DB, no bucket, no stored uploads/outputs. |
| Config/rules | Baked into the deployed image (`config/` ships with the app). Rule changes are maintainer-only, gated by `tools/regress.py`, shipped by redeploy. |

**Separation of who-touches-what (important):**
- Template `.xlsm` + Listungen `.xlsx` → uploaded per run, never stored → **any colleague**, in the UI.
- YAML rules + Python engine → in the deployed image → **maintainer only**, via redeploy.

## 4. The web app (what to build)

A single Streamlit page:

1. **Login** — handled by IAP before the app is even reached (see §5). App reads the
   authenticated email from the `X-Goog-Authenticated-User-Email` request header for
   display + audit logging.
2. **Two uploaders:** "BDF Listungen (.xlsx)" and "Fresh VC template (.xlsm)".
3. **Product-type / filter selection:** a Listungen sheet often covers several
   product types via its `Template` column, while the uploaded template is for one
   type. Replicate the Colab "Step 3" behaviour: after the Listungen is uploaded,
   show the distinct `Template` values with row counts, and let the user pick which
   to run (`filter_template`). Default the selection to the value matching the
   uploaded template's product type where detectable.
4. **Run button** → wrap `build()`. Simplest safe approach: write the two uploads to
   a `tempfile.TemporaryDirectory()`, call the **unmodified** `build()` with
   `config_dir` pointing at the shipped `config/`, then read the two output files
   back as bytes.
5. **Render the QA result in the page** (this is a required feature, not just the
   download). Render straight from the returned `QAReport` object — no need to
   re-parse the xlsx:
   - Big banner: green **UPLOAD READY** / red **NOT UPLOAD READY** from `report.status`.
   - `report.blockers()` list, `report.warnings()` (review items — scent/`Duft` is
     flagged every row by design, empty cost prices, GPSR gaps), `report.notes`.
   - The field-provenance table as a `st.dataframe` (every cell written + where the
     value came from). Source these from the `report.rows` / provenance structures —
     inspect `bdfvc/qa.py` for the exact accessors.
6. **Two download buttons:** filled `.xlsm` and QA `.xlsx`.
7. **Footer version stamp:** show a build id / deploy date so anyone can confirm
   what's live (reassurance for the single-source-of-truth model). Inject via build
   arg or env var at deploy time.

### Container / runtime notes
- `Dockerfile`: `python:3.12-slim`, `pip install -r requirements.txt` + `streamlit`,
  run `streamlit run app.py --server.port=8080 --server.address=0.0.0.0`. Cloud Run
  provides `$PORT` (default 8080) — bind to it.
- Streamlit uses websockets → deploy Cloud Run with **`--session-affinity`**.
- Behind IAP, set Streamlit `--server.enableCORS=false --server.enableXsrfProtection=false`
  (acceptable because IAP is the security boundary; document this).
- Resources: `--memory=1Gi --cpu=1` (openpyxl headroom), `--min-instances=0`
  (free; ~3–8s cold start), `--timeout=300`. Concurrency default is fine.
- Add `streamlit` (pin a version) to `requirements.txt`; keep `openpyxl`/`PyYAML`.

## 5. Auth setup (maintainer, in GCP console) — IAP path (recommended)

Google Workspace is confirmed, so this is the clean, zero-code route:
1. Configure the **OAuth consent screen** as **Internal** (restricts to the Remazing
   Workspace org automatically).
2. Enable APIs: Cloud Run, Cloud Build, Artifact Registry, **IAP**.
3. Deploy the Cloud Run service with **ingress = internal-and-cloud-load-balancing or
   all**, then **enable IAP on the Cloud Run service** (IAP now integrates directly
   with Cloud Run — verify the current console flow, as it has evolved).
4. Grant the role **IAP-secured Web App User** to **`domain:remazing.eu`** on the
   service. That single binding = "everyone at Remazing, nobody else."
5. Test: an `@remazing.eu` account gets in; any other Google account is denied by IAP
   before reaching the app.

**Fallback (app-level Google OIDC), only if IAP-on-Cloud-Run proves awkward:** use
Streamlit's native OIDC login (`st.login`) with a Google OAuth client, consent screen
set to **Internal**, and additionally assert the `hd` claim / email domain ==
`remazing.eu` in code. Store the client secret in **Secret Manager**. This is free but
adds code + a secret to manage; prefer IAP.

## 6. Off-limits / non-goals (protect the correctness guarantees)

- **Do not modify** `bdfvc/*` or `config/*` logic to make the web app work. Wrap the
  engine; write uploads to a temp dir and call `build()` unchanged. The tool's whole
  value is that its output is verified cell-by-cell against real human uploads
  (98.1% match, 0 blockers across 64 products) — a careless refactor forfeits that.
- **No AI** anywhere in the pipeline. It stays deterministic.
- **No persistence** of uploads or outputs. Stateless by design.
- Scope stays **de_DE only**. Do not add GB/English handling.
- **GPSR/compliance columns stay blank** (README §6). Out of scope for this build;
  the QA report already flags them. Note it as a roadmap item, don't invent values.

## 7. GCP resources checklist (the "what we need to run this")

- [x] GCP project + billing (already done by maintainer).
- [ ] Enable APIs: Cloud Run, Cloud Build, Artifact Registry, IAP.
- [ ] OAuth consent screen = Internal.
- [ ] Region europe-west3.
- [ ] Cloud Run service (from `gcloud run deploy --source .`).
- [ ] IAP enabled on the service + `domain:remazing.eu` → IAP-secured Web App User.
- [ ] No bucket, no DB, no Secret Manager (unless the OIDC fallback is used).
- Maintainer IAM: Owner (or Cloud Run Admin + IAP Policy Admin + Project IAM Admin).

## 8. Testing & acceptance

- [ ] `python tools/regress.py` still passes (64 products, cell-by-cell) — run before
  every deploy. Wire it as a pre-deploy check.
- [ ] Web run of a known Listungen + template reproduces the Colab output (diff the
  filled `.xlsm` and QA `.xlsx`).
- [ ] Multi-product-type Listungen: `filter_template` selection produces the right
  subset and row counts.
- [ ] Auth: `@remazing.eu` allowed; a personal Gmail denied.
- [ ] Cold-start run completes; downloads open correctly and reopen in Excel with
  cascading dropdowns intact (the xlsm.py concern).

## 9. Open decisions for the build session to confirm with the maintainer

1. **Auth: IAP (recommended, zero-code) vs app-level Google OIDC.** Default to IAP.
2. **`min-instances`: 0 (free, cold start) vs 1 (a few $/month, always warm).** Default
   to 0; it's a one-flag change later.
3. **Audit log (recommended, trivial):** log the IAP-authenticated email + filename +
   verdict per run to Cloud Logging (no storage to manage, within free tier). Confirm
   this is wanted for a shared tool.

## 10. Reference commands (indicative)

```bash
# from the project root, after building app.py + Dockerfile
gcloud config set project <PROJECT_ID>
gcloud run deploy bdf-vc-upload \
  --source . \
  --region europe-west3 \
  --memory 1Gi --cpu 1 \
  --min-instances 0 --timeout 300 \
  --session-affinity \
  --no-allow-unauthenticated
# then enable IAP on the service and grant:
#   member = domain:remazing.eu   role = roles/iap.httpsResourceAccessor
```

---

### Data / compliance reminder
Per Remazing's internal AI guidelines: keep personal data (names, emails, contact
details) out of these sheets and out of any AI tool — product and template data only.
The app must not persist uploads or outputs.
