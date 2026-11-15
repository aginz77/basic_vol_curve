# Deploying the IV surface site

```
browser ──► Cloudflare Access (GitHub login + email allowlist)
        ──► Cloudflare Tunnel ──► GCP e2-micro VM: gunicorn on 127.0.0.1:8000 (Flask app)
```

- The VM has **no open inbound ports**; `cloudflared` dials out to Cloudflare.
- Cloudflare Access lets in only GitHub accounts whose email is on the allowlist.
- The app also verifies Cloudflare's signed JWT on every request (`data/auth.py`), so
  nothing reaches it without passing Access.

Cost: $0 for GCP (e2-micro free tier) and Cloudflare (Zero Trust free plan, up to 50 users).
You need a domain on Cloudflare (see step 0).

---

## 0. Domain

Access protects hostnames on a domain whose DNS is on Cloudflare.

- **Free:** [eu.org](https://nic.eu.org) gives free domains like `yourname.eu.org`. Approval is
  manual and can take days to weeks. When asked for nameservers, use the two Cloudflare gives you.
- **Fast:** buy a cheap domain from any registrar (many suffixes are ~$1–3 for the first
  year), or at cost from Cloudflare Registrar.

Then: Cloudflare dashboard → **Add a domain** → Free plan → set the registrar's nameservers to
Cloudflare's → wait until the domain shows **Active**.

Below, `iv.example.com` is the hostname for the site.

## 1. Create the VM (Google Cloud)

Free-tier rules: **e2-micro**, region **us-west1 / us-central1 / us-east1**, standard
persistent disk ≤ 30 GB. Don't tick "Allow HTTP/HTTPS traffic"; it isn't needed.

Prerequisites: an **open billing account** linked to your project (free-tier VMs still
require one; reactivate it at console.cloud.google.com/billing if it shows as closed),
and gcloud pointed at a project you own:

```bash
gcloud projects list                          # find your project ID
gcloud config set project <PROJECT_ID>
gcloud billing accounts list                  # OPEN must be True
gcloud billing projects link <PROJECT_ID> --billing-account=<BILLING_ACCOUNT_ID>
gcloud services enable compute.googleapis.com
```

```bash
gcloud compute instances create ivsurface \
  --zone=us-central1-a --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard
```

## 2. Install the app on the VM

From your Mac, in the repo:

```bash
gcloud compute scp --recurse --zone=us-central1-a ./ ivsurface:~/basic_vol_curve
gcloud compute ssh ivsurface --zone=us-central1-a
# on the VM:
cd ~/basic_vol_curve && sudo bash deploy/setup_vm.sh
sudo nano /etc/ivsurface.env      # set MASSIVE_API_KEY now; the CF_ values come in step 5
```

To deploy code changes later: re-run the `scp`, then `sudo bash deploy/setup_vm.sh` again.

## 3. GitHub login

1. Cloudflare dashboard → **Zero Trust** (pick a team name; this gives you
   `<team>.cloudflareaccess.com`) → choose the Free plan.
2. GitHub → Settings → Developer settings → **OAuth Apps** → New OAuth App:
   - Homepage URL: `https://<team>.cloudflareaccess.com`
   - Authorization callback URL: `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`
   - Generate a client secret.
3. Zero Trust → **Settings → Authentication → Login methods → Add new → GitHub**, then paste
   the client ID and secret.

## 4. Create the email allowlist

1. Cloudflare → My Profile → **API Tokens** → Create Token → Custom:
   permission **Account → Zero Trust → Edit**, scoped to your account.
2. Your **Account ID** is on the domain's Overview page (right sidebar).
3. On your Mac:

```bash
export CF_API_TOKEN=...  CF_ACCOUNT_ID=...
python allowlist.py create you@your-github-email.com
export CF_ALLOWLIST_ID=<printed id>
```

Use the **primary email on each person's GitHub account**; that's what GitHub reports to
Cloudflare.

## 5. Protect the hostname with Access (do this before step 6)

Zero Trust → **Access → Applications → Add an application → Self-hosted**:

- Domain: `iv.example.com`
- Login methods: **GitHub only** (turn off One-time PIN)
- Policy: Action **Allow**, Include → **Emails in list** → `ivsurface-allowlist`

Save, then open the app's **Overview** and copy the **Application Audience (AUD) Tag**.
On the VM, put it in `/etc/ivsurface.env` with your team domain:

```
CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
CF_ACCESS_AUD=<AUD tag>
```

```bash
sudo systemctl restart ivsurface
```

## 6. Connect the VM with Cloudflare Tunnel

Zero Trust → **Networks → Tunnels → Create a tunnel → Cloudflared**, name it `ivsurface`:

1. Choose Debian, 64-bit, and run the install commands it shows **on the VM**. They install
   `cloudflared` as a service with your tunnel token.
2. **Public hostname**: subdomain `iv`, domain `example.com`, service `HTTP` → `localhost:8000`.

Open `https://iv.example.com`: you should see GitHub login, then the app with
"Signed in as …" at the top.

## Managing who has access

```bash
python allowlist.py list
python allowlist.py add friend@example.com
python allowlist.py remove friend@example.com
```

Changes apply to new logins straight away. Someone already signed in keeps access until their
Access session expires (24h by default; you can change it on the application).

## Troubleshooting

- `sudo journalctl -u ivsurface -f`: app logs.
- `sudo systemctl status cloudflared`: tunnel status.
- **500 "Auth is not configured"**: the `CF_ACCESS_*` values are missing from `/etc/ivsurface.env`.
- **403 after logging in**: `CF_ACCESS_AUD` doesn't match the application's AUD tag.
