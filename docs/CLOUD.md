# Running Dikdik on a server or cloud VM

Dikdik normally runs on your own computer and listens only to it. You can also run
it on a server (a cloud VM, a home server) and use it from any browser. This is for
**one owner**: everyone who has the sign-in link is you. A hosted, multi-user
"Dikdik Cloud" with accounts, teams and billing is a separate product that is not
built yet.

## What changes on a server

- Dikdik listens beyond `127.0.0.1` only when you pass `--listen`, and then it
  insists on `--public-url` (the exact `https://` address you will open). Requests
  for any other host or origin are refused.
- HTTPS is required: either built in (`--tls-cert` and `--tls-key`) or a reverse
  proxy in front that terminates TLS (`--behind-proxy`).
- The sign-in cookie is `Secure`, and ten failed sign-ins from one address lock it
  out for ten minutes.
- `--projects-root` keeps Open folder, New project and Clone inside one folder.
- The native folder picker and Open in VS Code are switched off (they would act on
  the server's desktop, not yours). Type folder paths instead.
- Commands the agent runs, tests and the headless browser run **on the server**.
  Treat the server as yours alone; do not share the sign-in link.

## Quick start on a VM (built-in HTTPS)

```bash
pip install git+https://github.com/<you>/dikdik    # or copy the repository and: pip install .
dikdik --listen 0.0.0.0 --port 443 \
  --public-url https://dikdik.example.com \
  --tls-cert /etc/letsencrypt/live/dikdik.example.com/fullchain.pem \
  --tls-key  /etc/letsencrypt/live/dikdik.example.com/privkey.pem \
  --projects-root ~/projects --no-browser
```

Open the sign-in link it prints (it uses your public URL) once per browser.
Model keys go in Settings → API keys, exactly as on your computer. Git uses the
server's own credentials (an SSH key or `gh auth login` on the server).

## Behind a reverse proxy, or with Docker

```bash
docker build -t dikdik .
docker run -d --name dikdik -p 127.0.0.1:8787:8787 \
  -e DIKDIK_PUBLIC_URL=https://dikdik.example.com \
  -v dikdik-projects:/projects -v dikdik-data:/data dikdik
docker logs dikdik    # the sign-in link
```

Point your proxy at `127.0.0.1:8787`, keep the original `Host` header, and
forward `Origin` unchanged. For example, Caddy: `dikdik.example.com { reverse_proxy 127.0.0.1:8787 }`.

## Status

Built-in HTTPS, the host/origin rules, Secure cookies, the sign-in limit, the
projects root and the disabled desktop features are covered by
`tests/test_remote.py` (HTTPS with a generated certificate on loopback). The
Dockerfile has **not** been built or run here, and no deployment on a real public
server has been tried yet. MCP sign-in callbacks still point at `127.0.0.1` and will
not complete on a remote server.
