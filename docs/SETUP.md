# Setup: step by step

About 45 minutes, done once. Do the steps in order. Each step tells you exactly what to copy and where to paste it.

You'll create 4 secrets, each a long code you paste into GitHub. Keep a notepad open while you work and **never text or email them to anyone.**

---

## Step 1: Make the database (Supabase)

1. Go to **supabase.com** and sign up with the company Google account.
2. Click **New project**.
   - Name: `smartstyle-sales`
   - Database password: click **Generate**, then copy it to your notepad.
   - Region: **East US (North Virginia)**
3. Wait about 2 minutes for it to finish.
4. Click **Connect** at the top. Choose **Session pooler**. Copy the URI. It looks like
   `postgresql://postgres.abcxyz:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres`
5. Replace `[YOUR-PASSWORD]` with the password from step 2. This is your **DATABASE_URL**.
6. **Plan:** the free plan pauses projects that go quiet and keeps no backups. Upgrade to **Pro ($25/mo)** under Settings → Billing. That gets you daily backups and it never pauses. Worth it.

## Step 2: Make the Jobber app

This gives the system read-only permission to see your quotes. You must be a Jobber **admin**.

1. Go to **developer.getjobber.com** and sign in with your Jobber login.
2. Click **Apps** → **New app**.
   - App name: `SmartStyle Reporting`
   - Description: `Internal sales reporting`
   - OAuth callback URL: `https://localhost/jobber` (type it exactly like this)
   - Scopes: turn on **read** for **Clients**, **Quotes** and **Users**. Leave everything else off.
3. Save. Copy the **Client ID** and **Client secret** to your notepad.

> Jobber may only allow API access on the **Plus** plan. If the Apps page won't let you continue, check your plan before you go further.

## Step 3: Put the secrets in GitHub

1. Open github.com/millerwilbur9/Wilbur → **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** for each of these:

| Name | Value |
|---|---|
| `DATABASE_URL` | from Step 1 |
| `JOBBER_CLIENT_ID` | from Step 2 |
| `JOBBER_CLIENT_SECRET` | from Step 2 |

## Step 4: Turn it on

The automatic schedule only runs from the repo's main branch.

1. Open the pull request **"Add sales schema…"** → click **Ready for review** → **Merge**.

## Step 5: Connect Jobber (once)

1. Paste this link into your browser, but put your Client ID where it says `YOUR_CLIENT_ID`:
   ```
   https://api.getjobber.com/api/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=https://localhost/jobber
   ```
2. Click **Allow**.
3. The next page will show an error like "site can't be reached." **That's normal.** Look at the address bar. It says `https://localhost/jobber?code=SOMETHING`. Copy only the part after `code=`.
4. **Do the next part within 5 minutes.** On GitHub, click **Actions** → **Connect Jobber** → **Run workflow** → paste the code → **Run workflow**.
5. Wait for the green check. Click into it. The "check" step lists 3 of your quotes. If you see them, you're connected.

If it turns red, copy the red error text and send it to whoever maintains this (or start a Claude session on this repo and paste it in).

## Step 6: Emails

Using a company Gmail account:

1. Google Account → **Security** → turn on **2-Step Verification** if it isn't on.
2. Search for **App passwords** → create one named `Sales report` → copy the 16 letters.
3. Add these GitHub secrets (same place as Step 3):

| Name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the Gmail address |
| `SMTP_PASSWORD` | the 16-letter app password |
| `REPORT_FROM` | the same Gmail address |

4. On GitHub open **config/report.yml** → click the pencil → type in the email addresses → **Commit changes**.
5. Test it: **Actions** → **Daily sales email** → **Run workflow**. Check your inbox.

## Step 7: Dashboard

Follow [LOOKER_STUDIO.md](LOOKER_STUDIO.md).

---

## What runs on its own after this

| What | When |
|---|---|
| Sync every quote from Jobber | Every hour at :17 |
| Daily email to reps and owners | Mon–Sat about 6 AM (7 AM in summer) |
| Tests | Every code change |

## When something goes wrong

- **GitHub emails you** when a sync fails. One failure is usually Jobber having a bad minute, and the next hour fixes it. **Three in a row** means something needs fixing.
- The daily email shows a **red warning** if data is more than 3 hours old.
- If the error says **"Reconnect Jobber"**, repeat Step 5.
- If the error says a **field doesn't exist**, Jobber changed something in its API. That's a small code fix. Paste the error into a Claude session on this repo.

## Cost

| Item | Monthly |
|---|---|
| Supabase Pro | $25 |
| GitHub Actions | $0 (about 1,200 of the 2,000 free minutes a private repo gets each month) |
| Looker Studio | $0 |
| Gmail | already have it |
