# Dikdik on a server or cloud VM, for one owner. See docs/CLOUD.md.
# Not yet built or run in this repository's checks (no Docker engine was available).
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates bash \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 dikdik

WORKDIR /opt/dikdik
COPY pyproject.toml README.md ./
COPY dikdik ./dikdik
RUN pip install --no-cache-dir . && mkdir -p /projects /data && chown dikdik:dikdik /projects /data

USER dikdik
VOLUME ["/projects", "/data"]
EXPOSE 8787
ENV DIKDIK_PUBLIC_URL=""
# A reverse proxy (Caddy, nginx, a cloud load balancer) terminates HTTPS in front of this.
CMD ["sh", "-c", "exec dikdik --listen 0.0.0.0 --port 8787 --public-url \"$DIKDIK_PUBLIC_URL\" --behind-proxy --projects-root /projects --data /data --no-browser"]
