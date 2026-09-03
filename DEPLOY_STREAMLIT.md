# Deploying BDF VC Upload to Streamlit Community Cloud

**Testing-phase host.** Free, no card, no billing address. The Cloud Run + IAP
runbook is parked in `DEPLOY_CLOUDRUN.md` and stays valid for later.

Everything here is maintainer-only. Colleagues just open the URL.

---

## What is different from the Cloud Run plan

| | Cloud Run + IAP | Community Cloud |
|---|---|---|
| Who can get in | IAM binding `domain:remazing.eu` -- whole org, one rule | An explicit list of email addresses you maintain by hand |
| Enforced by | Google, before the container is reached | Streamlit, before the app is reached |
| Where the data runs | Our own GCP project, `europe-west3` | Streamlit/Snowflake infrastructure, **US** |
| Cost | Needs a billing account | Free |
| Cold start | 3-8s | ~30s after a week idle |
| RAM | 1 GiB (set by us) | ~1 GB (set by them, not raisable) |

The last two rows in the middle column are the reason this is a testing host and
not the answer. **There is no domain-wide rule** -- a leaver keeps access until
you remove them from the viewer list by hand. Put a calendar reminder on it.

---

## 1. One-time: push to a **private** GitHub repo

The repo is already initialised with a first commit. Create the remote empty and
private, then push:

```bash
gh repo create bdf-vc-upload --private --source . --remote origin --push
```

No `gh`? Create it in the GitHub UI (**private**, no README, no .gitignore),
then:

```bash
git remote add origin git@github.com:<you-or-org>/bdf-vc-upload.git && git push -u origin main
```

Prefer creating it under the Remazing org rather than your personal account, so
it survives you.

**Check before pushing** that no client data is staged:

```bash
git ls-files | grep -iE '\.xls[xm]$' ; echo "^ empty = clean"
```

`.gitignore` already excludes `sample_output/` (it holds a filled Beiersdorf
deodorant workbook), the three working folders, and `.streamlit/secrets.toml`.
If that grep prints anything, stop and tell me.

---

## 2. Deploy

1. Go to <https://share.streamlit.io> and sign in **with GitHub**. Authorise it
   for the org that owns the repo, or it will not see a private repo.
2. **Create app** -> **Deploy from GitHub**.
3. Fill in:
   - Repository: `<you-or-org>/bdf-vc-upload`
   - Branch: `main`
   - Main file path: `app.py`
4. **Advanced settings**:
   - Python version: **3.12**. The code is clean of everything 3.12 removed
     (checked). If the build fails on a dependency, drop to 3.11.
   - Secrets: paste this, so the footer stamps a real build id instead of
     reading `dev / unbuilt`:
     ```toml
     BUILD_ID = "sc-001"
     BUILD_DATE = "2026-09-03"
     ```
     Streamlit copies top-level string secrets into the environment *before* the
     script runs, which is where `app.py` reads them. Verified.
5. **Deploy**. First build is a few minutes.

`requirements.txt` at the repo root is picked up automatically. There are no
system packages, so no `packages.txt` is needed.

---

## 3. One-time: make it private -- **do this before sharing the URL**

Fresh apps are **public**. Until you finish this step the link is open to anyone
who has it.

App menu (top right) -> **Settings** -> **Sharing** -> set the app to private /
"only specific people can view", then add each colleague's `@remazing.eu`
address and save.

Two things to know:

- The free tier caps how many **private** apps one account can run. If the
  private option is greyed out or refused, that is the quota, not a bug -- and
  it is a hard stop: do not hand out a public URL as a workaround. Tell me and
  we will look at Hugging Face Spaces (private Space, also free).
- Each viewer signs in with the address you listed. They need a Google login on
  that address, which `@remazing.eu` Workspace accounts have.

---

## 4. Acceptance checks

Walk all of these before telling colleagues the URL.

- [ ] The sidebar "Signed in as" line shows **your own email**. This is the
      whole security check in one line: the address only appears when the app is
      private and the allowlist is live. If it reads
      `unknown (app not private / local run)`, **the app is still public.** Stop
      and finish step 3.
- [ ] An address *not* on the list is refused by Streamlit, before the app
      loads. Test it -- personal Gmail, or ask a colleague you did not add.
- [ ] Footer build id matches the `BUILD_ID` secret you pasted.
- [ ] A known Listungen + fresh template reproduces the Colab output. Diff the
      filled `.xlsm` and the QA `.xlsx` against a Colab run of the same inputs.
- [ ] **Open the downloaded `.xlsm` in Excel and click into
      `Produktunterkategorie`.** The cascading dropdown must still work. This is
      the `bdfvc/xlsm.py` splice and it is still unproven -- see "Known gap" in
      `DEPLOY_CLOUDRUN.md`. Host-independent; it did not get safer by moving.
- [ ] A multi-product-type Listungen: the picker's row counts match and the
      chosen type writes the right subset.
- [ ] A **realistically large** Listungen. Community Cloud gives ~1 GB and
      openpyxl is memory-hungry; on Cloud Run we chose 1 GiB deliberately and
      could have raised it. Here you cannot. If the app restarts mid-run, that
      is the ceiling, and it is a reason to go back to Cloud Run rather than
      something to tune.

---

## 5. Routine operations

**Ship a rule change** -- edit the YAML in `config/`, then:

```bash
git add -A && git commit -m "rules: <what changed>" && git push
```

Community Cloud redeploys on push. The next person to open the URL is on the new
version. Bump `BUILD_ID` in Settings -> Secrets when you want the footer to
prove which version is live.

Run the regression check before you push -- it is not wired into this path the
way `deploy.sh` wired it into Cloud Run, so nothing will stop you shipping a
break:

```bash
BDF_FIXTURES=~/path/to/fixtures .venv/bin/python tools/regress.py
```

**Add or remove a viewer** -- Settings -> Sharing. Removal is manual and is the
only offboarding there is.

**Roll back** -- `git revert <sha> && git push`. There is no revision-traffic
knob like Cloud Run's.

**Logs** -- "Manage app" in the bottom right of the running app. Streamlit keeps
a live tail only; there is no queryable audit log, so the one-line-per-run
`run user=` records are not retained the way `gcloud logging read` retained
them. If an audit trail matters for the assessment, note it as missing.

**Wake it** -- after ~a week idle the app sleeps; the first visitor waits ~30s
and may see a "wake up" button. Harmless, but warn colleagues so it does not
read as broken.

---

## 6. Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
BUILD_ID=local .venv/bin/streamlit run app.py
```

The sidebar reads `unknown (app not private / local run)` -- expected off a
private deployment.

---

## 7. For the later assessment

Carry these into the decision, because they are the things this host costs us:

1. **Client data leaves our infrastructure.** Beiersdorf listing files and the
   filled templates transit and are processed on Streamlit/Snowflake US
   infrastructure. Nothing is stored by the app, but the processing itself
   happens there. This is the item to clear with whoever owns data processing.
2. **Access is a hand-maintained list**, not a domain rule. It drifts.
3. **No retained audit log.**
4. **A fixed ~1 GB memory ceiling** we cannot raise.

None of the four is fixed by configuration. All four go away on the Cloud Run
path in `DEPLOY_CLOUDRUN.md`, which needs one thing: a billing account.
