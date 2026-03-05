# Infrastructure: Cloud vs Physical Machines

## Decision Framework

### When to use Cloud

| Factor | Cloud wins when... |
|--------|-------------------|
| **Scale** | Workload is bursty or unpredictable |
| **Models** | You need frontier models (Opus, o3, Gemini Pro) |
| **Team size** | Small team, no ops capacity |
| **Compliance** | Provider has required certifications (SOC2, HIPAA) |
| **Time-to-market** | Need to start immediately, no hardware procurement |
| **Geographic distribution** | Team in multiple regions |

### When to use Physical Machines

| Factor | Physical wins when... |
|--------|----------------------|
| **Volume** | Steady, high-volume workload (>$500/mo cloud) |
| **Privacy** | Code/data must not leave your network |
| **Latency** | Need sub-100ms inference |
| **Cost** | Long-term cost optimization (>6mo horizon) |
| **Control** | Need to fine-tune or customize models |
| **Air-gapped** | Security requirements prohibit internet access |

## Infrastructure Tiers

### Tier 1: Pure Cloud (simplest, fastest to start)

```
Developer Machine
  └─→ Orchestrator (local Python process)
        ├─→ Anthropic API (Claude)
        ├─→ OpenAI API (GPT)
        └─→ Google AI API (Gemini)
```

**Cost**: API usage only
**Setup time**: Minutes
**Maintenance**: Zero
**Best for**: Solo devs, small teams, prototyping

### Tier 2: Hybrid (local + cloud)

```
Developer Machine
  └─→ Orchestrator (local)
        ├─→ Local GPU Server (Ollama/vLLM)
        │     └── Llama 70B, Codestral
        ├─→ Anthropic API (complex tasks)
        └─→ OpenAI API (fallback)
```

**Hardware**:
- 1x GPU server: 2x RTX 4090 (48GB total) — ~$3,000
- Or: 1x RTX 5090 (32GB VRAM) — ~$2,000
- Or: Mac Studio M4 Ultra (192GB unified) — ~$5,000 (runs 70B at decent speed)

**Cost**: $3-5K upfront + ~$50/mo electricity + reduced API costs
**Setup time**: Days
**Maintenance**: Low (Ollama auto-updates, Ubuntu LTS)
**Best for**: Teams of 3-10, cost-conscious, privacy-aware

### Tier 3: Full On-Prem (maximum control)

```
Developer Machines
  └─→ Orchestrator Service (Kubernetes)
        ├─→ vLLM Cluster (local GPUs)
        │     ├── Node 1: 4x A100 80GB
        │     ├── Node 2: 4x A100 80GB
        │     └── Load Balancer
        ├─→ Cloud API (frontier-only fallback)
        └─→ Model Registry (MLflow)
```

**Hardware**:
- GPU cluster: $50-200K depending on scale
- Networking, cooling, rack space
- Staff for maintenance

**Cost**: High upfront, low marginal
**Setup time**: Weeks to months
**Maintenance**: High (dedicated MLOps needed)
**Best for**: Enterprise, >20 devs, strict compliance, high volume

## Recommended Configurations

### Solo Developer / Freelancer

| Component | Choice | Cost |
|-----------|--------|------|
| Orchestrator | Local Python | Free |
| Primary provider | Claude Sonnet (API) | ~$50-100/mo |
| Cheap tasks | Gemini Flash (API) | ~$5/mo |
| Optional local | Mac with Ollama | Existing hardware |
| **Total** | | **~$55-105/mo** |

### Small Team (3-5 devs)

| Component | Choice | Cost |
|-----------|--------|------|
| Orchestrator | Local service (Docker) | Free |
| Complex tasks | Claude Sonnet/Opus (API) | ~$200/mo |
| Standard tasks | GPT-4o (API) | ~$100/mo |
| Simple tasks | 1x GPU server + Ollama | $3K once + $50/mo |
| **Total** | | **~$350/mo + $3K setup** |

### Medium Team (10-20 devs)

| Component | Choice | Cost |
|-----------|--------|------|
| Orchestrator | Kubernetes service | ~$100/mo (infra) |
| Complex tasks | Claude Opus/Sonnet (API) | ~$500/mo |
| Standard tasks | vLLM cluster (2x A100) | $40K once + $200/mo |
| Simple tasks | Same cluster, smaller model | Included |
| **Total** | | **~$800/mo + $40K setup** |

## GPU Buying Guide (2026)

### Consumer GPUs (best value for small setups)

| GPU | VRAM | Price | Can Run | Tok/s (70B Q4) |
|-----|------|-------|---------|-----------------|
| RTX 4090 | 24GB | ~$1,600 | 34B full, 70B Q4 | ~20 |
| RTX 5090 | 32GB | ~$2,000 | 70B Q4 comfortably | ~35 |
| RTX 4060 Ti 16GB | 16GB | ~$400 | 22B full, 34B Q4 | ~15 |

### Workstation/Server GPUs

| GPU | VRAM | Price | Can Run | Tok/s (70B) |
|-----|------|-------|---------|-------------|
| A100 80GB | 80GB | ~$10K (used) | 70B full precision | ~50 |
| H100 80GB | 80GB | ~$25K | 70B full, blazing fast | ~120 |
| L40S 48GB | 48GB | ~$7K | 70B Q8 | ~40 |

### Apple Silicon (unified memory advantage)

| Machine | RAM | Price | Can Run | Tok/s (70B Q4) |
|---------|-----|-------|---------|-----------------|
| Mac Mini M4 Pro | 48GB | ~$2,000 | 70B Q4 | ~12 |
| Mac Studio M4 Ultra | 192GB | ~$5,000 | 70B full, 405B Q4 | ~25 |
| Mac Pro M4 Ultra | 192GB | ~$7,000 | Same + expandable | ~25 |

## Network Architecture for Hybrid Setup

```
┌──────────────┐      HTTPS/WSS      ┌──────────────────┐
│  Dev Machine │ ◄──────────────────► │  Cloud APIs       │
│              │                      │  (Anthropic,      │
│  Orchestrator│                      │   OpenAI, Google) │
│              │                      └──────────────────┘
│              │
│              │      LAN (fast)      ┌──────────────────┐
│              │ ◄──────────────────► │  GPU Server       │
│              │                      │  - Ollama/vLLM    │
└──────────────┘                      │  - Llama 70B      │
                                      │  - Codestral      │
                                      └──────────────────┘
```

**Key requirements**:
- GPU server on same LAN for low latency (<5ms)
- Cloud APIs via standard HTTPS
- Orchestrator handles routing, retry, fallback logic
- No special networking needed (all standard HTTP)
