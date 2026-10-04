# Deploy fastapi-mqtt-gateway on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.
The Deployment has exactly one replica and uses `Recreate`: rolling updates
must not overlap two clients with the same `MQTT_CLIENT_ID`.
A startup probe allows up to five minutes for cold imports on a busy worker
before liveness checks take over. The 250m CPU request preserves a scheduling share.

Default MQTT broker: cluster Mosquitto in `homeassistant`
(`mosquitto.homeassistant.svc.cluster.local:1883`).

## Apply

Use the `deploy/k3s-mp` Kustomization, which pairs the probes with the tested 0.1.3
image built from source `6a50ca2`, pinned by digest. Prepare an ignored local copy
and set `images[].newName` in its overlay to the registry holding that exact
image. The committed registry hostname is deliberately a placeholder.
Customize the broker ConfigMap in the same local copy when necessary.

```bash
mkdir -p .local-private
chmod 700 .local-private
cp -R deploy/k3s deploy/k3s-mp .local-private/
# Edit .local-private/k3s-mp/kustomization.yaml: set the real registry.
kubectl kustomize .local-private/k3s-mp
```

Create the real Secret out-of-band before applying. The Kustomization deliberately
excludes `02-secret.example.yaml`, so an ordinary apply cannot overwrite existing
credentials with placeholders. It also excludes the optional placeholder Ingress;
configure a real ingress host and TLS in a private overlay if external access is
needed. The ClusterIP Service is available without an Ingress.

```bash
kubectl create namespace mqtt-gateway --dry-run=client -o yaml | kubectl apply -f -
# Initial installation only: create fails if the Secret already exists.
# Supply broker credentials here when the broker requires authentication.
kubectl -n mqtt-gateway create secret generic fastapi-mqtt-gateway \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=API_USERNAME=gateway \
  --from-literal=API_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=MQTT_USERNAME='' \
  --from-literal=MQTT_PASSWORD=''

kubectl apply -k .local-private/k3s-mp
kubectl -n mqtt-gateway get pods -o wide   # expect NODE=mp
```

For an update, skip Secret creation and retain the existing broker and API
credentials. If a credential is missing or invalid, patch only that key; do not
reapply empty broker values or generate replacements for working credentials.

Apply the configured overlay, not the unpinned base. Keep the digest when setting
the registry; use a new verified digest when upgrading, and roll back the image
and its compatible probes together. Existing deployments from before API credential
validation must provide `API_USERNAME`, `API_PASSWORD`, and a non-placeholder
`JWT_SECRET_KEY`; existing broker credentials are separate and must be preserved.

The container runs as UID/GID 10001 with a read-only root filesystem and a
separate writable `/tmp` volume. Deploy the updated image together with these
manifests. Token lifetime settings use `ACCESS_TTL_MINUTES` and
`REFRESH_TTL_DAYS` in the ConfigMap; the Deployment maps them to the existing
`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` and `JWT_REFRESH_TOKEN_EXPIRE_DAYS` application
environment variables. Carry over any customized TTL values when updating.

`/health` is process liveness and reports `mqtt_connected`. `/ready` returns 503
when the broker is disconnected, removing the Pod from service routing while
Paho reconnects without a liveness restart loop. Acceptance requires one Ready
Pod on `mp`, `mqtt_connected: true`, authenticated API access, and a publish/read
round trip on an isolated test topic; a Running Pod alone is insufficient.
