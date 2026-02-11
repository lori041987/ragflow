# Anthropic Gateway Debug Record (RAGFlow)

## Summary
Two independent issues blocked connectivity to the company Anthropic gateway:

1. **Routing conflict** between Docker’s bridge subnet and the gateway’s `172.17.x.x` network.
2. **API header/auth mismatch**: the gateway rejects `x-api-key` and injects an invalid `anthropic-beta` header.

Both were resolved by:
- Moving the RAGFlow Docker network to a non-overlapping subnet (`10.201.0.0/16`).
- Bypassing LiteLLM for Anthropic when `Authorization: Bearer` is required and overriding Anthropic headers.

---

## Environment
- Host has multiple NICs; the gateway is reachable only via `enp2s0`.
- Gateway: `http://t1cim-wncchat.wneweb.com.tw/anthropic`
- Gateway IP: `172.17.101.97`
- RAGFlow running via Docker Compose.

---

## Debug Timeline

### 1) Routing Issue
**Symptom:**
- `ping` only works with `-I enp2s0`.
- From inside container: `curl` to gateway fails with “No route to host.”

**Commands used:**
```
ip route
```
```
docker network inspect docker_ragflow --format '{{.IPAM.Config}}'
```
```
docker exec -it docker-ragflow-cpu-1 ip route
```
```
docker exec -it docker-ragflow-cpu-1 curl -v http://t1cim-wncchat.wneweb.com.tw/anthropic
```

**Observed logs:**
- `docker_ragflow` network subnet was `172.17.0.0/16`.
- Container default route: `via 172.17.0.1`.
- `curl` failed: `No route to host`.

**Root cause #1:**
- The container network overlapped with the gateway’s `172.17.101.97`.
- Docker treated the destination as local bridge traffic and never forwarded to the host/NIC.

**Fix #1:**
- Set the RAGFlow network to a non-overlapping subnet:

```
networks:
  ragflow:
    driver: bridge
    ipam:
      config:
        - subnet: 10.201.0.0/16
```

**Verification:**
- `docker network inspect docker_ragflow --format '{{.IPAM.Config}}'` shows `10.201.0.0/16`.
- `curl` to gateway now connects (HTTP 404 instead of no route).

**Routing explanation:**
- Before: container network `172.17.0.0/16` made `172.17.101.97` appear local, so traffic stayed on Docker bridge.
- After: container network moved to `10.201.0.0/16`, so traffic routes to host gateway and then out via `enp2s0`.

**Summary:**

What was happening before

- Your gateway resolves to 172.17.101.97.
- Docker’s bridge network was 172.17.0.0/16.
- So the container saw 172.17.101.97 as local to its own bridge, and tried to route it inside the container network via
  eth0 instead of sending it out to the host.
- That resulted in No route to host.

Why 10.201.0.0/16 fixed it

- Now the container network is 10.201.0.0/16.
- 172.17.101.97 is no longer “local” to the container.
- The container sends it to its default gateway (10.201.0.1), which is the Docker host.
- The host then routes it via its own routing table — and your host has a route to that IP via enp2s0.

So the traffic goes:

container -> docker bridge -> host -> enp2s0 -> gateway

It’s not pinning enp2s0 directly; it just stops the container from mistakenly treating 172.17. as local*.


---

### 2) API Header/Auth Issue
**Symptom:**
- Web UI “Save model provider” fails with authentication error.
- LiteLLM error: `api_key is not supported.`

**Commands used:**
```
curl -v http://t1cim-wncchat.wneweb.com.tw/anthropic/v1/messages
```
```
curl -v http://t1cim-wncchat.wneweb.com.tw/anthropic/v1/messages \
  -H "x-api-key: <key>" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-3-5-haiku-20241022","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}'
```
```
curl -v http://t1cim-wncchat.wneweb.com.tw/anthropic/v1/messages \
  -H "Authorization: Bearer <key>" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-3-5-haiku-20241022","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}'
```

**Observed logs:**
- `x-api-key` → `401` with `"api_key is not supported."`
- `Authorization: Bearer` → `400` with error about invalid `anthropic-beta` header:
  `Unexpected value(s) 'web-search-2025-03-05' for the 'anthropic-beta' header`

**Root cause #2:**
- Gateway does **not** accept `x-api-key` (LiteLLM default for Anthropic).
- Gateway injects an **invalid** `anthropic-beta` header, breaking normal Anthropic requests.

**Fix #2:**
- Add a direct Anthropic HTTP path (bypassing LiteLLM) when:
  `RAGFLOW_ANTHROPIC_AUTH=authorization`
- Use `Authorization: Bearer` and allow override of `anthropic-version` / `anthropic-beta`.

**Runtime env used:**
```
RAGFLOW_ANTHROPIC_AUTH=authorization
RAGFLOW_ANTHROPIC_VERSION=2023-06-01
RAGFLOW_ANTHROPIC_BETA=
```

**Result:**
- Requests succeed through the gateway.
- Web UI model save works.

---

## Logs Used for Debugging
- Host routing:
  - `ip route`
- Docker network/subnet:
  - `docker network inspect docker_ragflow --format '{{.IPAM.Config}}'`
- Container routing:
  - `docker exec -it docker-ragflow-cpu-1 ip route`
- HTTP verification:
  - `curl -v .../anthropic` and `.../anthropic/v1/messages`
- RAGFlow logs:
  - `docker logs --tail 200 docker-ragflow-cpu-1`

---

## Files Changed
- `docker/docker-compose-base.yml`: moved ragflow network to `10.201.0.0/16`
- `docker/docker-compose.yml`: bind-mount patched `rag/llm/chat_model.py`
- `docker/.env`: add Anthropic Authorization + header overrides
- `rag/llm/chat_model.py`: direct Anthropic HTTP path + env-controlled headers

---

## Final Notes
- The routing fix prevents Docker from hijacking `172.17.x.x` traffic.
- The auth fix is required because the company gateway rejects `x-api-key` and injects a bad `anthropic-beta` header.
- The direct Anthropic path is only enabled when `RAGFLOW_ANTHROPIC_AUTH=authorization` is set; other providers remain on LiteLLM.
