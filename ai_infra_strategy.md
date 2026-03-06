# Strategia Infrastruttura AI Multi-Agent
**Piano Go-Live & Roadmap di Scaling**
*Versione 1.0 — Marzo 2026*

---

## Executive Summary

Questo documento descrive la strategia per costruire e scalare un sistema di orchestrazione multi-agent basato su LLM open-source, partendo da un'infrastruttura conservativa a basso costo su AWS + OpenRouter, con un piano chiaro per evolvere verso un setup ibrido self-hosted non appena il progetto genera ricavi sufficienti.

**Budget di partenza:** ~€42/mese
**Soglia di scaling:** €600/mese di ricavi
**Obiettivo finale:** Infrastruttura ibrida AWS + Vast.ai H200 a ~€625/mese

---

## 1. Situazione Attuale & Analisi dei Costi

### Opzioni valutate

| Soluzione | Costo/mese | Pro | Contro |
|---|---|---|---|
| Claude Max $200 | ~€185 | Zero setup, Opus 4.6 incluso | Rate limit, no fine-tuning, dati su Anthropic |
| AWS + Vast.ai H200 (12h/giorno) | ~€625 | Privato, fine-tuning, illimitato | Costo fisso alto, overhead ops |
| **AWS + OpenRouter Qwen3 30B** | **~€42** | Minimo costo, zero infra GPU | Pay-per-token, no fine-tuning |

### Costi dettagliati — Fase 1 (Conservativa)

| Voce | $/mese | €/mese |
|---|---|---|
| EC2 t3.medium (orchestrator 24/7) | $30 | €28 |
| EBS 50GB + Elastic IP + transfer | $12 | €11 |
| AWS S3 storage modelli/dati | $0 | €0 (free tier iniziale) |
| OpenRouter Qwen3 30B — inferenza | ~$3 | ~€3 |
| **Totale stimato** | **~$45** | **~€42** |

*Stima basata su ~18M input token + 4.5M output token/mese (agenti 12h/giorno)*

### Prezzi OpenRouter Qwen3 30B A3B

- Input: **$0.08 / 1M token**
- Output: **$0.28 / 1M token**
- Nessun costo fisso, nessun rate limit restrittivo per uso normale

---

## 2. Architettura — Fase 1 (Go-Live Conservativo)

```
[Client / UI]
      |
      v
[AWS EC2 t3.medium — Orchestrator]
  - LangGraph / CrewAI
  - API Gateway (FastAPI)
  - Redis cache (ElastiCache free tier)
  - Job scheduler (agenti autonomi)
      |
      v
[OpenRouter API]
  - Qwen3 30B A3B (inferenza, agenti)
  - Fallback: Qwen3.5-Flash (task semplici, costo minore)
      |
      v
[AWS S3]
  - Storage dati, prompt templates, output
```

### Stack tecnologico

- **Orchestrazione:** LangGraph (workflow stateful) o CrewAI (role-based agents)
- **LLM Gateway:** OpenRouter (unico endpoint, multi-model routing)
- **Modello principale:** Qwen3 30B A3B Instruct — $0.08/$0.28 per 1M token
- **Modello economico:** Qwen3.5-Flash — $0.10/$0.40 per 1M token (task semplici)
- **Infra:** AWS EC2 t3.medium, S3, Elastic IP
- **Linguaggio:** Python 3.11+, FastAPI, Docker

---

## 3. Piano Go-Live — Fasi

### Fase 1: MVP Conservativo (Mese 1-2)
**Budget: ~€42/mese**

**Obiettivi:**
- Deploy orchestrator su EC2 t3.medium
- Integrazione OpenRouter con Qwen3 30B
- Primo workflow agente funzionante end-to-end
- Monitoraggio costi token (dashboard semplice)

**Task tecnici:**
1. Setup EC2 t3.medium con Docker
2. Deploy LangGraph + FastAPI
3. Configurare OpenRouter API key e routing
4. Implementare 1-2 agenti specializzati (es. ricerca + sintesi)
5. Setup S3 per persistenza dati
6. Alert budget OpenRouter (cap mensile)

**KPI di successo:**
- Sistema live e stabile
- Costo mensile < €60
- Latenza media risposta agente < 5 secondi

---

### Fase 2: Ottimizzazione & Primi Ricavi (Mese 2-4)
**Budget: €42-100/mese**

**Obiettivi:**
- Routing intelligente tra modelli (Qwen3 30B per task complessi, Flash per semplici)
- Prompt caching per ridurre costi token ripetuti
- Acquisizione primi clienti/utenti paganti

**Task tecnici:**
1. Implementare prompt caching (risparmio 50-80% su contesti ripetuti)
2. Routing automatico per complessità del task
3. Rate limiting per utente
4. Logging e analytics utilizzo

**KPI di successo:**
- Ricavi mensili > €100
- Costo per richiesta ottimizzato
- NPS utenti positivo

---

### Fase 3: Scaling Ibrido (quando ricavi > €600/mese)
**Budget: €625/mese**

**Trigger di attivazione:** Ricavi netti mensili superano €600 per 2 mesi consecutivi.

**Obiettivi:**
- Aggiungere GPU Vast.ai H200 per inferenza ad alte prestazioni e fine-tuning
- Mantenere OpenRouter come fallback e per traffico di picco
- Iniziare fine-tuning su dati proprietari

**Architettura Ibrida:**

```
[AWS EC2 — Orchestrator]
      |
      |--- Task complessi / fine-tuned ---> [Vast.ai H200 — vLLM]
      |
      |--- Task standard / burst ---------> [OpenRouter Qwen3]
      |
      |--- Task semplici / economici -----> [OpenRouter Qwen3.5-Flash]
```

**Costi Fase 3:**

| Voce | €/mese |
|---|---|
| AWS EC2 + S3 + networking | €80 |
| Vast.ai H200 interruptible (252h/mese inferenza) | €305 |
| Vast.ai H200 on-demand (108h/mese fine-tuning) | €241 |
| OpenRouter (overflow/fallback) | €30 stima |
| **Totale** | **~€656** |

---

## 4. Analisi Break-Even

### Quando conviene passare al self-hosted?

Il self-hosted H200 diventa conveniente **esclusivamente per il costo GPU** quando:

> Spesa mensile OpenRouter > €545 (costo GPU Vast.ai)

Con Qwen3 30B a $0.08/$0.28 per 1M token, ci si arriva a circa:

- **~7.8 miliardi di input token/mese** in parità pura di costo GPU
- Equivalente a ~260 milioni di token/giorno — **uso enterprise massivo**

**Conclusione:** La motivazione per passare al self-hosted NON è il risparmio token ma:
1. **Fine-tuning** su dati proprietari (impossibile con OpenRouter)
2. **Privacy totale** dei dati (dati sensibili che non devono uscire dall'infra)
3. **Latenza garantita** senza dipendenza da terze parti
4. **Modello custom** fine-tunato per il proprio dominio

---

## 5. Gestione Rischi

| Rischio | Probabilità | Impatto | Mitigazione |
|---|---|---|---|
| Aumento prezzi OpenRouter | Media | Medio | Multi-provider routing, fallback DashScope |
| Rate limit OpenRouter | Bassa | Alto | Cache aggressiva, tier upgrade |
| Interruzione Vast.ai (Fase 3) | Media | Medio | OpenRouter come fallback automatico |
| Costi token fuori controllo | Media | Alto | Budget cap hard su OpenRouter, alert |
| EC2 downtime | Bassa | Alto | Auto-recovery con CloudWatch + Lambda |

---

## 6. Stack di Monitoraggio

### Fase 1 (minimal)
- **AWS CloudWatch:** metriche EC2 (CPU, RAM, uptime)
- **OpenRouter dashboard:** token usage, costi per modello
- **Script Python custom:** alert Telegram/email se costo giornaliero > soglia

### Fase 3 (completo)
- **Grafana + Prometheus:** metriche infra complete
- **LangSmith o LangFuse:** tracing agenti LLM
- **Vast.ai dashboard:** utilizzo GPU, uptime istanze

---

## 7. Roadmap Sintetica

```
MESE 1-2         MESE 2-4          MESE 4+              MESE 6+
MVP Go-Live  --> Ottimizzazione --> Primi Ricavi     --> Scaling Ibrido
€42/mese         €42-100/mese       €100-600/mese        €625+/mese

AWS + OpenRouter  Routing smart    Clienti paganti      + Vast.ai H200
Qwen3 30B         Prompt caching   Fine-tuning piano    Modello custom
1-2 agenti        Analytics        SLA definiti         Full autonomy
```

---

## 8. Decisioni Chiave

| Decisione | Scelta | Motivo |
|---|---|---|
| Modello LLM | Qwen3 30B A3B | Miglior rapporto qualità/costo per agenti, open-weight |
| Orchestratore | LangGraph | Workflow stateful, cicli, human-in-the-loop |
| Provider iniziale | OpenRouter | Unico endpoint, multi-model, zero overhead infra |
| Cloud infra | AWS | Affidabilità, familiarità, ecosistema maturo |
| GPU (Fase 3) | Vast.ai H200 | Prezzo più basso mercato, interruptible ok |
| Storage modelli | AWS S3 | $0.023/GB, affidabile, integrazione nativa |

---

## 9. Prossimi Passi Immediati

1. **Settimana 1:** Setup EC2 t3.medium, Docker, FastAPI base
2. **Settimana 1:** Account OpenRouter, test Qwen3 30B, verifica latenza
3. **Settimana 2:** Deploy LangGraph orchestrator, primo agente funzionante
4. **Settimana 2-3:** Definire il primo use case di prodotto (cosa fanno gli agenti?)
5. **Settimana 3-4:** Beta test con primi utenti, raccolta feedback
6. **Mese 2:** Pricing del servizio, onboarding primi clienti paganti

---

*Documento redatto: Marzo 2026 — da aggiornare a ogni fase completata*
