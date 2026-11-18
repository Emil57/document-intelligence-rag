# Deploy to AWS Lightsail

The app runs on one small Linux server: Docker runs the app, and Caddy in front of it handles
HTTPS. Every push to `main` runs the tests, builds a new Docker image and starts it on the server,
using the GitHub Actions workflow in `.github/workflows/deploy.yml`.

```
 push to main ─► GitHub Actions: tests ─► build image ─► GitHub container registry
                                                              │
                                                              ▼ (SSH)
                 browser ─► https ─► Lightsail server: Caddy ─► app ─► /data volume
```

**Why a server and not a "serverless" AWS product?** The app keeps its database and uploaded
files on disk, and its search indexes and chats in memory, so it needs one copy running all the
time with a disk that persists. App Runner and Lambda have no persistent disk, and the Lightsail
container service loses its files on every deploy.

You do steps 1–6 once. They take about 30–45 minutes.

## 1. Create the server

1. In the [Lightsail console](https://lightsail.aws.amazon.com/), choose **Create instance**.
2. Pick a region near your users, **Linux/Unix**, **OS Only**, and **Ubuntu 24.04 LTS**.
3. Pick a plan with at least **1 GB of memory**. The 512 MB plans are too tight for Docker, Caddy
   and the app together.
4. Name it, for example `docintel`, and create it.
5. **Networking** tab:
   - **Create static IP** and attach it to the instance, so the address never changes.
   - Under **IPv4 Firewall**, add a rule for **HTTPS (TCP 443)**. SSH (22) and HTTP (80) are
     open by default. HTTP is needed for the certificate and to redirect to HTTPS.

## 2. Give it a domain name

Caddy needs a domain name to get an HTTPS certificate.

- **With your own domain:** at your domain provider, add an `A` record, for example
  `docs.example.com`, pointing to the static IP.
- **Without a domain:** use the free [sslip.io](https://sslip.io) service, which turns an IP into
  a name. For the IP `3.120.45.67`, the name is `3-120-45-67.sslip.io`.

## 3. Install Docker on the server

Open the server's terminal with **Connect using SSH** in the Lightsail console, then run:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

Connect again, so the `docker` group applies, and check that `docker compose version` works.

## 4. Add the app's settings on the server

The settings, including your API key, stay on the server and are never stored in GitHub.

```bash
mkdir -p ~/app
nano ~/app/.env
```

Put in:

```
ANTHROPIC_API_KEY=sk-ant-...
DOCINTEL_DOMAIN=docs.example.com
DOCINTEL_SIGNUP_CODE=choose-a-long-random-code
DOCINTEL_USER_DAILY_LIMIT_USD=0.50
DOCINTEL_APP_DAILY_LIMIT_USD=5.00
```

Save with Ctrl+O, Enter, Ctrl+X, then make the file readable only by you:

```bash
chmod 600 ~/app/.env
```

Always set `DOCINTEL_SIGNUP_CODE` on a public server, and share it only with people you invite.
There's no limit on login attempts yet, so also ask people to use long passwords.

## 5. Let GitHub Actions log in to the server

GitHub Actions needs its own SSH key, separate from yours.

**On your own computer**, create the key:

```bash
ssh-keygen -t ed25519 -f docintel_deploy -N "" -C "github-actions deploy"
```

This makes two files: `docintel_deploy`, the private key for GitHub, and `docintel_deploy.pub`,
the public key for the server.

**On the server**, allow that key to log in. Paste the whole line from `docintel_deploy.pub`
between the quotes:

```bash
echo "ssh-ed25519 AAAA... github-actions deploy" >> ~/.ssh/authorized_keys
```

Still on the server, print its own key, which GitHub uses to check it's talking to the real server:

```bash
echo "$(curl -s https://checkip.amazonaws.com) $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
```

Copy the line it prints, which looks like `3.120.45.67 ssh-ed25519 AAAA...`.

## 6. Add the secrets and settings to GitHub

In the repository on GitHub, open **Settings → Environments → New environment** and name it
`production`. Under **Deployment branches**, you can limit it to `main`.

In that environment, add these **secrets**:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | The static IP, for example `3.120.45.67` |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | The whole content of the private key file `docintel_deploy` |
| `DEPLOY_KNOWN_HOSTS` | The line printed at the end of step 5 |

And these **variables**:

| Variable | Value |
|---|---|
| `DEPLOY_DOMAIN` | The domain from step 2 |
| `DEPLOY_ENABLED` | `true` |

Then delete the private key file `docintel_deploy` from your computer; GitHub has the copy it needs.

## 7. Deploy

Open **Actions → Test and deploy → Run workflow**, or push a commit to `main`. When it finishes,
open `https://<your domain>` and sign up with your sign-up code.

The first deploy takes a few minutes. Later ones are faster, because unchanged parts of the image
are reused.

## Backups

Your accounts, documents and usage live in the `appdata` Docker volume on the server. Turn on
**Automatic snapshots** in the instance's **Snapshots** tab in Lightsail. They cost a little extra,
and a snapshot can be restored into a new server if something goes wrong.

## Day-to-day

Run these on the server, in `~/app`:

| To | Run |
|---|---|
| See the app's logs | `docker compose logs -f app` |
| See if it's running and healthy | `docker compose ps` |
| Change a setting | Edit `.env`, then `docker compose up -d` |
| Restart | `docker compose restart app` |

## If something goes wrong

- **The workflow skips the deploy job:** check that the `DEPLOY_ENABLED` variable is `true` and
  that you pushed to `main`.
- **SSH fails with "Host key verification failed":** `DEPLOY_KNOWN_HOSTS` doesn't match the
  server. Redo the last part of step 5.
- **"Check the site answers" fails:** run `docker compose logs caddy` on the server. Usually the
  domain doesn't point to the static IP yet (DNS changes can take a while), or port 443 isn't open.
- **The image pull fails with "denied":** in GitHub, open the package under your profile's
  **Packages**, and under **Package settings → Manage Actions access**, give this repository
  read access.
