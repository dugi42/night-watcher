#!/bin/sh
# Tailscale startup wrapper for Docker Compose.
#
# Wraps the official containerboot entrypoint:
#   1. Starts tailscaled + runs `tailscale up` (via containerboot).
#   2. Waits until the daemon reports BackendState == Running.
#   3. Fetches a TLS certificate for the Tailscale hostname.
#   4. Writes cert.pem / key.pem to /certs (shared volume for nginx).
#
# Required environment variables:
#   TS_AUTHKEY   — Tailscale auth key (create at https://login.tailscale.com/admin/settings/keys)
#   TS_HOSTNAME  — Machine name in your tailnet (default: night-watcher-pi)
set -e

HOSTNAME="${TS_HOSTNAME:-night-watcher-pi}"

# Start the official Tailscale container entrypoint in the background.
# containerboot reads TS_AUTHKEY, TS_HOSTNAME, etc. and calls `tailscale up`.
/usr/local/bin/containerboot &
CONTAINERBOOT_PID=$!

echo "[tailscale] Waiting for Tailscale daemon to connect (hostname: ${HOSTNAME})..."
ATTEMPTS=0
until tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"'; do
    ATTEMPTS=$((ATTEMPTS + 1))
    if [ $ATTEMPTS -ge 60 ]; then
        echo "[tailscale] ERROR: Tailscale did not connect after 120 s — check TS_AUTHKEY" >&2
        kill $CONTAINERBOOT_PID 2>/dev/null
        exit 1
    fi
    sleep 2
done
echo "[tailscale] Connected."

# Fetch the TLS certificate issued by Tailscale's ACME endpoint.
# Requires HTTPS certificates to be enabled for the tailnet
# (Tailscale admin console → DNS → Enable HTTPS Certificates).
echo "[tailscale] Fetching TLS certificate for ${HOSTNAME}..."
mkdir -p /certs
tailscale cert \
    --cert-file=/certs/cert.pem \
    --key-file=/certs/key.pem \
    "${HOSTNAME}"
echo "[tailscale] Certificate written to /certs/cert.pem + /certs/key.pem"

# Forward signals to containerboot and wait for it to exit.
wait $CONTAINERBOOT_PID
