# Add BAAI Rerank Provider — Actual Steps (Repo)

This is the **real implementation** used in this repo to add **BAAI** as a new rerank provider, including the step that avoids default masking.

## 1) Add BAAI factory + model in config
**File:** `conf/llm_factories.json`

Added a factory block for BAAI:
```json
{
  "name": "BAAI",
  "logo": "",
  "tags": "TEXT RE-RANK",
  "status": "1",
  "rank": "921",
  "llm": [
    {
      "llm_name": "bge-reranker-base",
      "tags": "RE-RANK,8k",
      "max_tokens": 8196,
      "model_type": "rerank"
    }
  ]
}
```

## 2) Implement the BAAI rerank client
**File:** `rag/llm/rerank_model.py`

Added the real class used in this repo:
```python
class BAAIRerank(Base):
    _FACTORY_NAME = "BAAI"

    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("Rerank url cannot be None")
        if "/rerank" not in base_url:
            base_url = urljoin(base_url, "/v1/rerank")
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
            "Authorization": f"Bearer {key}",
        }
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        if len(texts) == 0:
            return np.array([]), 0
        texts = [truncate(t, 4096) for t in texts]
        token_count = num_tokens_from_string(query) + sum([num_tokens_from_string(t) for t in texts])
        data = {"model": self.model_name, "query": query, "texts": texts}
        verify = os.environ.get("RAGFLOW_RERANK_SSL_VERIFY", "").strip().lower()
        verify_flag = not (verify in {"0", "false", "no"})
        res = httpx.post(self.base_url, headers=self.headers, json=data, verify=verify_flag).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res.get("data", []):
                rank[d["index"]] = d.get("score", d.get("relevance_score", 0.0))
        except Exception as _e:
            log_exception(_e, res)
        return rank, token_count
```

**Why this matches your gateway**
- POST to `.../v1/rerank`
- Uses `Authorization: Bearer <api_key>`
- Payload: `{"model":..., "query":..., "texts":...}`

## 3) Mount updated files into the container
**File:** `docker/docker-compose.yml`

Added mounts so container uses your local updates:
```
../conf/llm_factories.json:/ragflow/conf/llm_factories.json
../rag/llm/rerank_model.py:/ragflow/rag/llm/rerank_model.py
```

## 4) Avoid default masking of BAAI
**File:** `api/apps/llm_app.py`

Factory list endpoint filters certain providers:
```python
fac = [f.to_dict() for f in fac if f.name not in ["Youdao", "FastEmbed", "Builtin"]]
```

**Action:** Ensure **BAAI is NOT in this exclusion list**.

## 5) Reload factories into DB
Factories are stored in DB; reload after edits:
```bash
docker exec docker-ragflow-cpu-1 python - <<'PY'
from api.db.init_data import init_llm_factory
init_llm_factory()
print("LLM factory list reloaded")
PY
```

## 6) Enable `base_url` field in UI for BAAI
**File:** `web/src/pages/user-setting/setting-model/modal/api-key-modal/index.tsx`

Add BAAI to `modelsWithBaseUrl`:
```ts
const modelsWithBaseUrl = [
  LLMFactory.OpenAI,
  LLMFactory.AzureOpenAI,
  LLMFactory.TongYiQianWen,
  LLMFactory.MiniMax,
  LLMFactory.BAAI,
];
```

## 7) Build frontend and mount dist
```bash
cd web
npm install
npm run build
```

Mount:
```
../web/dist:/ragflow/web/dist
```

## 8) Restart containers
```bash
docker compose -f ./docker/docker-compose.yml up -d
```

## 9) Clear browser cache (if UI still old)
- Chrome/Edge: `Ctrl+Shift+R`
- DevTools → Reload → **Empty Cache and Hard Reload**
- Or open in Incognito

---

**Result**
- BAAI shows in Available Models
- BAAI rerank works against custom gateway
- `base_url` field shows in UI after cache refresh
