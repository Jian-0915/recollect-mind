# Recollect

**Long-term memory for conversations that knows *whose* memory it is.**

Most "AI memory" layers embed text chunks, retrieve the top-k by cosine similarity, and paste them into the prompt. That works until the conversation lasts more than a few sessions, because real dialogue breaks vector search in three specific ways:

- **Misattribution.** "My friend's dog is called Pudding" comes back as *"your dog Pudding"*. "My nephew started kindergarten" is filed as *a fact about you*. A similarity index has no notion of whose memory an event is.
- **Unbounded growth.** Every turn is stored forever and ranks equally, so last year's small talk crowds out what actually matters today.
- **Overwrite instead of update.** A new statement about the same thing either duplicates or silently replaces the old one, so "what did I say before?" can no longer be answered.

Recollect is a memory layer that treats these as first-class problems, on top of a single SQLite file. It ships as an **MCP server**, an **HTTP API + web UI**, and an **in-process SDK**.

> Status: a working prototype (schema v21), not a hosted service. The Python package is still named `neuromem` internally — the repo rename is in progress. See [Roadmap](#roadmap).

## Clean public build & API keys

**This repository ships with no account, no API key, and no private endpoints.** It is a clean build you can clone and publish safely:

- **No keys in source.** There is no real `sk-` key anywhere in the code. Your own key lives only in local `data/*.db` (`settings` table, Windows DPAPI-encrypted with a `dpapi:` prefix), and both `data/` and `*.db` are gitignored — so clone/commit/push never carry it.
- **No personal model params.** Model names and endpoints are all overridable via environment variables; see [`.env.example`](.env.example). With no overrides, a generic local-Ollama default applies (in `config.py`) that belongs to nobody.
- **First-run guidance.** On first open (while the memory store is empty), the overview page shows a banner with the next step for your mode: local-Ollama users see the model-pull commands, cloud users are prompted to enter **their own** key. It disappears once you have memories, and can be dismissed manually.

Pick one of two setups:

1. **Local Ollama (no key, recommended to start):** install Ollama, pull the models below, and it just works.
2. **Cloud OpenAI-compatible endpoint (SenseNova / DeepSeek / DashScope / Moonshot / OpenAI, etc.):** in the web "Models" page pick a preset or enter base_url + model names, then paste **your own API key**. The key stays on your machine only.

> To preset model names via env (never the key): copy `.env.example` to `.env` and edit the `NEUROMEM_*` values. Real environment variables take precedence over `.env` (handy for CI/tests). `.env` itself is gitignored.

---

## Models & where to get them

Two kinds of models are needed; each can be local or cloud (recommended combos at the end):

| Role | Purpose | Default (local) | Requirement |
|---|---|---|---|
| LLM extract | turns dialogue into structured memories / affect / profiles | `qwen3.5:4b` | fast, cheap, emits JSON |
| LLM answer | generates replies and personas | `qwen3:8b` | stronger than extract, steadier |
| Embedding | vectorizes memories; decides retrieval quality | `qwen3-embedding:4b` | **dimension must match the store (2560)** |

> The embedding model is the **least safe thing to swap** in this system: changing the model or the
> dimension makes old memories unretrievable, and cosine search silently truncates to the shorter
> dimension without an error. Unless you are willing to clear and re-ingest the store, stick with local 4B.

### Choosing an embedding model

| Option | Model | Dim | Best for |
|---|---|---|---|
| **Local Ollama (recommended)** | `qwen3-embedding:4b` | 2560 | best Chinese memory retrieval, free, data stays local; this project's default |
| Local Ollama | `bge-large:latest` | 1024 | weaker machines, faster/cheaper |
| DashScope / Qwen | `text-embedding-v3` | 1024 | already on Alibaba Cloud, good Chinese, CN-direct |
| SiliconFlow | `BAAI/bge-large-zh-v1.5` | 1024 | one key for both cloud LLM and embeddings |
| OpenAI | `text-embedding-3-small` | 1536 | all-OpenAI stack, English/multilingual |

In the web "Models" page, under "Embedding model", **click a recommendation to fill the form** (fill only, no auto-save), confirm, then click "Save & apply" and verify with "Test embedding". Changing dimension requires clearing and re-ingesting the store first.

> SenseNova's Token Plan OpenAI-compatible gateway (`https://token.sensenova.cn/v1`) has **no embeddings endpoint**; its "Piccolo Embedding" belongs to a separate SenseCore API needing its own key/base_url. So when using SenseNova chat/extract models, keep embeddings on local Ollama — do not call `/embeddings` on the same base_url.

### Option A: local Ollama (free, no key — recommended to start)

1. Install Ollama: https://ollama.com (Windows/macOS/Linux, next-next-finish).
2. Pull the models (first run downloads them; the embed model is ~2 GB):
   ```bash
   ollama pull qwen3-embedding:4b    # embedding, 2560-dim
   ollama pull qwen3.5:4b            # extraction
   ollama pull qwen3:8b              # answers (optional; 4B is fine for speed)
   ```
3. No key needed — just `python -m neuromem serve 8000`.

> Switching the embedding model requires a matching **dimension** or old memories become unretrievable. The default store is 2560-dim (4B).

### Option B: cloud OpenAI-compatible endpoints (bring your own key)

In the web "Models" page click a preset (it fills base_url + model names), then paste **your own API key**. All providers below speak the OpenAI protocol — only base_url + key + model name differ:

| Provider | Get a key (console) | base_url | Recommended (extract / answer) | Notes |
|---|---|---|---|---|
| **DeepSeek** (recommended) | https://platform.deepseek.com | `https://api.deepseek.com` | `deepseek-chat` / `deepseek-chat` | fast, cheap, free credits on signup |
| **SenseNova (SenseTime)** (author's own) | https://platform.sensenova.cn | `https://token.sensenova.cn/v1` | `sensenova-6.8-flash-lite` / `sensenova-6.8-flash` | CN-direct; signup credits, dedicated Flash-Lite credit pool |
| DashScope / Qwen | https://bailian.console.aliyun.com | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` | stable, new-user credits |
| Moonshot | https://platform.moonshot.cn | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | Kimi series |
| SiliconFlow | https://cloud.siliconflow.cn | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` / `Qwen/Qwen2.5-72B-Instruct` | try many models with one key, free tier |
| OpenAI | https://platform.openai.com/api-keys | `https://api.openai.com/v1` | `gpt-4o-mini` / `gpt-4o-mini` | needs overseas network + payment |

**The author's own setup (SenseNova)** — the combo this project was tuned with; copy it as-is:

| Role | Model ID | Why |
|---|---|---|
| Extract | `sensenova-6.8-flash-lite` | fast, low credit cost; turns dialogue into structured memories |
| Answer | `sensenova-6.8-flash` | steadier replies and persona generation |

In the web "Models" page click the `sensenova` preset — it fills base_url and both model names; you only paste **your own key**. Create one at https://platform.sensenova.cn → console → "API Keys" (`sk-` prefix).

> Getting a key, generally: sign up → open the console's "API Keys" page → create and copy it (usually shown once — save it immediately). Provider names and model IDs change over time; trust what the console shows. If you get `model not found`, copy the exact ID from the provider's model list.

**Recommended combos**: start with the full local-Ollama stack (zero cost). For stronger generation, use `sensenova-6.8-flash-lite` for extract + `sensenova-6.8-flash` for answer (the author's own setup, one key, CN-direct), or DeepSeek for both. Keep the embedding model local 4B to avoid dimension mismatches and cloud embedding cost.

---

## How it differs

| | Typical vector memory | Recollect |
|---|---|---|
| Attribution | none — text chunks | every event carries `subject`: `self` / `counterpart` / `shared` / `third_party` |
| Forgetting | TTL or nothing | continuous decay `act *= exp(-Δt/τ)`, τ derived from salience, emotion, surprise, confidence, relationships, review count |
| Updating | overwrite | **reconsolidation**: old gist + vector kept in `memory_versions`, re-encoded with the current context, reversible |
| Counting questions ("how many times…") | top-k can't count | aggregation layer (`count` / `list` / `relation_count`) over the full matching set |
| Answer safety | none | every candidate's source text is checked against the reply; unsupported sentences are trimmed |
| Observability | none | adoption log, per-turn trace, mind state, and an A/B lab |

## Mechanisms

| Layer | What it does | Code |
|---|---|---|
| L0 constitution | who the agent is, who it serves, tone, hard limits. Human-versioned, read-only at runtime, exempt from reset and decay | `identity.py`, `persona_card` |
| L1 events | extracted events with `subject`, time, place, affect, surprise | `store.py` |
| Concepts / graph | entity clustering, concept induction, diffusion activation at recall | `consolidator.py`, `graph.py` |
| Recall | vector + entity + graph diffusion + fusion + credibility tiers, with a relevance floor | `recall.py` |
| Forgetting | activation decay + salience compensation + archiving | `forgetting.py` |
| Reconsolidation | versioned rewrite on feedback, 1h cooldown, 12-version cap | `adoption.py` |
| Affect | 8-dimensional discrete emotion + slow mood + per-person affect | `affect.py` |
| Aggregation | answers count/list/relation questions from the full set instead of top-k | `aggregate.py` |
| Fact guard | trims sentences the evidence doesn't support; refuses instead of inventing | `agent/companion.py` |

```
conversation turn
      │
      ▼
 Brain.turn()  ──►  recall  (vector + entity + graph + fusion)
      │                    │
      │                    ▼
      │              ContextPack  ──►  LLM  ──►  structured output
      │                                              │
      ▼                                              ▼
 Brain.commit()  ◄──  adopted ids / new facts / affect / mind state
      │
      ├─► events (+subject, surprise, affect)
      ├─► adoption_log  ──► reconsolidation (memory_versions)
      └─► trace (latency, candidates, used_ids, think tokens)
```

## Quick start

Requires Python 3.11+. The default setup uses [Ollama](https://ollama.com) locally (no API key needed); you can point the chat/embedding models at any OpenAI-compatible endpoint in the web UI using your own key. Model names are configurable via `.env` — see [Clean public build](#clean-public-build--api-keys).

```bash
pip install -r requirements.txt
ollama pull qwen3-embedding:4b        # default local embed model (2560-dim, strong on Chinese); configurable

python -m neuromem init               # idempotent: tables + defaults
python tools/seed_demo.py             # optional: 12 fictional memories to look at
python -m neuromem serve 8000         # http://127.0.0.1:8000
```

On first login, create an admin account in the browser. If you later switch to a cloud model without a key, the overview page will guide you to add your own.

Chat with memory in the web UI, or call the API:

```bash
curl -X POST http://127.0.0.1:8000/api/live -H "Content-Type: application/json" \
     -d '{"query":"我朋友养的狗叫什么？"}'
```

Read-only recall (does not write memory) is `POST /api/ask`; MCP's `memory_retrieve` uses the same path.

## MCP

```json
{
  "mcpServers": {
    "recollect": {
      "command": "python",
      "args": ["/absolute/path/to/tools/mcp_server.py"]
    }
  }
}
```

Ten tools: `memory_retrieve`, `memory_remember`, `memory_ask`, `memory_person`, `memory_recent`, `memory_status`, `memory_state`, `memory_turn`, `memory_surface`, `memory_feedback`.

`memory_surface` returns memories worth bringing up unprompted; `memory_feedback` reports which candidates were actually used, which is what triggers reconsolidation (exposure ≠ adoption).

## In-process (for companion agents)

```python
from neuromem.brain import Brain

brain = Brain()
pack = brain.turn("我叫小林，在杭州做设计")
# ... generate a reply from pack.text ...
brain.commit(pack, used_ids=[...], remember=[...])
```

`agent/companion.py` is a full reference implementation: it decides `used_ids` itself, writes facts, and records a trace per turn.

## Does it actually work?

Honesty first: **there is no public benchmark number yet** (LoCoMo / LongMemEval are the next milestone, see Roadmap). What exists today is engineering verification:

- **364 unit tests**, all green on a fresh clone with no local database (`pytest tests/unit`)
- an 8-question regression harness with expected-evidence checks (`tools/eval_regression.py`)
- ~150 turns of real usage traces from the web channel, used to find and fix four recall bugs and one attribution bug (see [docs/DEVLOG.md](docs/DEVLOG.md))
- ablation via `NEUROMEM_LLM_THINK=on`, `AGGREGATE_LAYER=0`, etc. — mechanisms are switchable, so they can be measured rather than asserted

## Repository layout

```
neuromem/     memory core (store, recall, forgetting, reconsolidation, affect, aggregate)
agent/        reference companion agent + prompts + trace
tools/        MCP server, evaluation harnesses, probes, migrations
web/          single-page management UI
tests/        364 unit tests
docs/         architecture notes and the development log
```

## Roadmap

- [ ] LoCoMo / LongMemEval results, with an ablation table
- [ ] Docker one-liner + seeded demo database (no Ollama required to click around)
- [ ] Embedding fallback (hashing / small model) so the first run isn't a 2.5 GB download
- [ ] Package rename (`neuromem` → the new name) and PyPI release
- [ ] English + Chinese docs split, architecture diagrams

## Contributing

Issues and PRs are welcome, especially: Chinese long-conversation test cases, attribution edge cases (kinship, pets, groups), and benchmark adapters. Please run `pytest tests/unit` before sending a PR — the suite is the contract.

## Support

If Recollect has been useful, the web UI has a "Support" page with QR codes (entirely optional — all features are free). QR images live in `web/donate/` (`wechat.png` / `alipay.png` / `coffee.png`) and are committed with the repo, so anyone who clones can see them. Prefer to contribute instead? Issues, Chinese long-conversation test cases, and benchmark adapters are worth far more — see Contributing above.

## License

Apache-2.0. See [LICENSE](LICENSE).

---

**[中文说明](README.zh.md)** · **[开发日志 docs/DEVLOG.md](docs/DEVLOG.md)**
