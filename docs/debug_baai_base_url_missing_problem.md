# BAAI Base URL Field Missing in UI — Debug Record

## Symptom
- In Model Provider UI (`/user-setting/model`), selecting **BAAI** shows only `api_key`.
- The `base_url` input is missing.

## Debug Process

### 1) Verify backend factory exists
**Goal:** Confirm BAAI factory is present in DB and in `llm_factories.json`.

**Commands used**
- `docker exec docker-ragflow-cpu-1 grep -n "\"name\": \"BAAI\"" /ragflow/conf/llm_factories.json`
- `docker exec docker-ragflow-cpu-1 python -c "from api.db.services.tenant_llm_service import LLMFactoriesService; print([f.name for f in LLMFactoriesService.get_all()][:50])"`

**Findings**
- `BAAI` exists in DB.
- `conf/llm_factories.json` contains a BAAI block with `TEXT RE-RANK` and `bge-reranker-base`.

### 2) Verify UI source supports base_url for BAAI
**Goal:** Confirm UI code shows base_url when `llmFactory === BAAI`.

**Command used**
- `rg -n "modelsWithBaseUrl|BAAI" web/src/pages/user-setting/setting-model/modal/api-key-modal/index.tsx`

**Findings**
- `BAAI` is included in `modelsWithBaseUrl`.
- UI logic should render `base_url` for BAAI.

### 3) Verify built bundle contains updated UI
**Goal:** Ensure deployed JS bundle contains new UI logic.

**Command used**
- `grep -R -n 'BAAI|modelsWithBaseUrl|base_url' /ragflow/web/dist`

**Findings**
- Built JS contains BAAI and `modelsWithBaseUrl` references.

### 4) Compare browsers
**Observation**
- Different browser shows `base_url` correctly.

**Conclusion**
- The issue is browser cache, not backend or code.

## Root Cause
- Browser cached an older JS bundle that did not include the BAAI `base_url` logic.

## Fix / Resolution
- Clear cache or force a hard reload:
  - Chrome/Edge: `Ctrl+Shift+R`
  - DevTools: right-click reload icon → **Empty Cache and Hard Reload**
  - Firefox: `Ctrl+Shift+R`
- Or open the page in Incognito/Private mode.
- Or append a cache-busting query string:
  - `http://192.168.25.249:8080/user-setting/model?cache_bust=1`

## Logs / Evidence Used
- `conf/llm_factories.json` includes:
  - `name: "BAAI"`
  - `tags: "TEXT RE-RANK"`
  - `llm_name: "bge-reranker-base"`
- DB query confirms BAAI exists in `LLMFactoriesService`.
- UI source confirms `BAAI` in `modelsWithBaseUrl`.
- Built JS confirmed to include BAAI logic.
- Alternate browser shows correct UI.

## Tools Used (Explanation)
- `rg` / `grep`: Search source and built JS to verify code inclusion.
- `docker exec ... python -c`: Query DB to verify factory records.
- `docker exec ... grep`: Check factory config in JSON.
- Browser cross-check: Validated caching as the actual cause.

## Final Status
- Resolved. `base_url` appears after cache refresh.
