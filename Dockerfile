# syntax=docker.io/docker/dockerfile:1
#
# Self-contained OpenHost packaging of Cap (https://github.com/CapSoftware/cap).
# Runs the whole open-source Loom as ONE rootless container: Cap's web app +
# bundled MySQL 8 + MinIO (S3 API) + a Caddy front-proxy, all data on the
# instance's persistent disk. Nothing leaves the box.
#
# Cap officially requires MySQL 8 — its migrations use JSON-function GENERATED
# columns that MariaDB rejects — so we run on a glibc base (Ubuntu) with real
# mysql-server, and reuse Cap's prebuilt (pure-JS) web app artifacts rather than
# rebuilding the monorepo from source.

# --- Cap web app: OUR fork's build (CarlKho-Minerva/cap @ carl/openhost-selfhost) ---
# Built natively (amd64+arm64) by the fork's "Docker Build Web" GitHub Action from
# apps/web/Dockerfile and pushed to ghcr. Differs from upstream CapSoftware/cap only
# by the OpenHost self-host changes: trusted-proxy SSO auto-login (no email code) and
# the "Cap Pro" upsell hidden off Cap Cloud (NEXT_PUBLIC_IS_CAP unset at build).
# Pinned by the multi-arch index digest so a redeploy can't silently change Cap or
# re-run migrations. To update: re-run the Action, then bump this digest deliberately.
FROM ghcr.io/carlkho-minerva/cap-web@sha256:a6808d48125cd1d6b342d6a4edb59fdfdc271a29f95feaf38cd5767e9d2ce1ca AS capweb

# --- Cap's official media-server (Bun + FFmpeg): transcoding, HLS, thumbnails, Loom import ---
# Pinned to the digest that was `:latest` as of 2026-07-17 (see cap-web note above).
FROM ghcr.io/capsoftware/cap-media-server@sha256:43587203aa3be503ab290fe8e6fbb32da7916dba56326044b44f99adbc8cdf10 AS mediaserver

# --- glibc runtime: MySQL 8 + Node 24 + MinIO + Caddy ---
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# MySQL 8 server/client (core packages avoid the systemd/postinst datadir dance),
# plus tools. mysql-server-core provides /usr/sbin/mysqld; mysql-common creates
# the `mysql` system user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       mysql-server-core-8.0 mysql-client-core-8.0 mysql-common \
       ffmpeg ca-certificates curl xz-utils tar \
    && id mysql >/dev/null 2>&1 || (groupadd -r mysql && useradd -r -g mysql -s /usr/sbin/nologin mysql) \
    && rm -rf /var/lib/apt/lists/*

# Node 24 (glibc) via NodeSource — matches the version Cap's app was built with.
# The setup script is fetched at a fixed nodesource/distributions commit and the
# nodejs package pinned to an exact version; bump NODEJS_VERSION deliberately.
ARG NODESOURCE_REF=c6e581b0d24e5d043476ddb947d70e6fe10e83c9
ARG NODEJS_VERSION=24.22.1-1nodesource1
RUN curl -fsSL "https://raw.githubusercontent.com/nodesource/distributions/${NODESOURCE_REF}/scripts/deb/setup_24.x" -o /tmp/setup_node.sh \
    && echo "6e3d580f5bd7ccf2aa1e8df8d35c60d78e873c3ff8beb282c9bebd914904ad72  /tmp/setup_node.sh" | sha256sum -c - \
    && bash /tmp/setup_node.sh && rm /tmp/setup_node.sh \
    && apt-get install -y --no-install-recommends "nodejs=${NODEJS_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && node --version

# MinIO server + Caddy, matched to the host architecture.
# dl.min.io and MinIO's container images are gone; the official release binaries
# remain on the (archived) minio/minio GitHub releases, verified by sha256.
# Caddy is checked against the sha512 in its release's caddy_2.11.7_checksums.txt.
ARG MINIO_RELEASE=RELEASE.2025-09-07T16-13-09Z
RUN set -eux; \
    case "$(uname -m)" in \
      x86_64) A=amd64; MINIO_SHA256=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f; \
        CADDY_SHA512=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7 ;; \
      aarch64) A=arm64; MINIO_SHA256=5c83cd2cf151717ba0243f73e1c7802ff36e272b67144bdd7f1f7d684fd6f03d; \
        CADDY_SHA512=3db36ba90c7a6e8dda40ee3dd71fa08844c76b5fb08f61b31e5e78d2ed38e71c51dc7baed875e50d1ca1279196e84302967237386ae87c91ae9f2aaceada682e ;; \
      *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/minio/minio/releases/download/${MINIO_RELEASE}/minio.linux-${A}.${MINIO_RELEASE}" -o /usr/local/bin/minio; \
    echo "${MINIO_SHA256}  /usr/local/bin/minio" | sha256sum -c -; \
    chmod +x /usr/local/bin/minio; /usr/local/bin/minio --version; \
    curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_${A}.tar.gz" -o /tmp/caddy.tgz; \
    echo "${CADDY_SHA512}  /tmp/caddy.tgz" | sha512sum -c -; \
    tar -xzf /tmp/caddy.tgz -C /usr/local/bin caddy; rm /tmp/caddy.tgz; caddy version

# Cap's web app (standalone) lives at /app (server at /app/apps/web/server.js).
COPY --from=capweb /app /app

# Next's image optimizer needs a glibc-native sharp (the copied one is musl).
RUN cd /app && npm install --no-audit --no-fund sharp@0.34.5 \
    && node -e "require('sharp'); console.log('sharp glibc OK')"

# Bundled media-server: copy the Bun runtime + the app. Both stages are glibc
# (Debian/Ubuntu), so the native node-av addon is ABI-compatible; it uses the
# system ffmpeg installed above. Runs as a loopback process on :3456.
COPY --from=mediaserver /usr/local/bin/bun /usr/local/bin/bun
COPY --from=mediaserver /app /opt/media-server

COPY Caddyfile /etc/caddy/Caddyfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# The OpenHost router terminates TLS and forwards plain HTTP to this port.
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
