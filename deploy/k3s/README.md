# Deploy fastapi-mqtt-gateway on k3s (`mp`)

Pinned to Mac Pro worker via `nodeSelector: kubernetes.io/hostname: mp`.

Default MQTT broker: cluster Mosquitto in `homeassistant`
(`mosquitto.homeassistant.svc.cluster.local:1883`).

## Apply

```bash
kubectl create namespace mqtt-gateway --dry-run=client -o yaml | kubectl apply -f -
kubectl -n mqtt-gateway create secret generic fastapi-mqtt-gateway \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=MQTT_USERNAME='' \
  --from-literal=MQTT_PASSWORD='' \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k deploy/k3s
kubectl -n mqtt-gateway get pods -o wide   # expect NODE=mp
```

Image: `ghcr.io/4alvit/fastapi-mqtt-gateway:latest` (`.github/workflows/docker-publish.yml`).
