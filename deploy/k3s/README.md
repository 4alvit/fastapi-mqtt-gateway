# Deploy fastapi-mqtt-gateway on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.
The Deployment has exactly one replica and uses `Recreate`: rolling updates
must not overlap two clients with the same `MQTT_CLIENT_ID`.

Default MQTT broker: cluster Mosquitto in `homeassistant`
(`mosquitto.homeassistant.svc.cluster.local:1883`).

## Apply

Create the real Secret out-of-band before applying. The Kustomization deliberately
excludes `02-secret.example.yaml`, so an ordinary apply cannot overwrite existing
credentials with placeholders. It also excludes the optional placeholder Ingress;
configure a real ingress host and TLS in a private overlay if external access is
needed. The ClusterIP Service is available without an Ingress.

```bash
kubectl create namespace mqtt-gateway --dry-run=client -o yaml | kubectl apply -f -
kubectl -n mqtt-gateway create secret generic fastapi-mqtt-gateway \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=API_USERNAME=gateway \
  --from-literal=API_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=MQTT_USERNAME='' \
  --from-literal=MQTT_PASSWORD='' \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k deploy/k3s
kubectl -n mqtt-gateway get pods -o wide   # expect NODE=mp
```

Pin a tested image digest in the deployment overlay before applying; do not deploy
an unverified mutable `latest` tag. Existing deployments from before API credential
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
