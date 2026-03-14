# 30 - Adoption Roadmap

Based on the analysis, here is a prioritized roadmap for incorporating DeerFlow learnings into our agent-orchestrator.

## Phase 1: Safety & Resilience (Priority: Critical)

### 1.1 Loop Detection Middleware
- Implement tool call hashing and sliding window tracking
- Warn threshold: 3 identical calls
- Hard stop: 5 identical calls
- Per-session tracking with LRU eviction
- **Effort**: 1 day
- **Impact**: Prevents token-burning infinite loops

### 1.2 Dangling Tool Call Recovery
- Detect AIMessage tool_calls without matching responses
- Inject placeholder ToolMessages
- Handle user-interrupted flows gracefully
- **Effort**: 0.5 days
- **Impact**: Better UX for interrupted conversations

### 1.3 Tool Description Parameter
- Add `description` parameter to skill interfaces
- Force agents to explain WHY they're calling tools
- Improve audit log readability
- **Effort**: 1 day
- **Impact**: Better debugging and observability

## Phase 2: Context Efficiency (Priority: High)

### 2.1 Progressive Skill Loading
- List skills by name/description in system prompt
- Agent reads full skill instructions on-demand
- Reduces base system prompt from ~20K to ~4K tokens
- **Effort**: 2 days
- **Impact**: Significant token savings, supports more skills

### 2.2 Configurable Context Summarization
- Add trigger types: tokens, messages, fraction
- Configurable retention policy (keep last N messages)
- Use lightweight model for summarization
- **Effort**: 2 days
- **Impact**: Enables longer multi-step tasks

## Phase 3: Developer Experience (Priority: High)

### 3.1 Embedded Client
- Create `OrchestratorClient` for programmatic access
- Same API as REST endpoints
- No HTTP overhead
- Enable scripting and notebook usage
- **Effort**: 3 days
- **Impact**: New use cases, easier testing

### 3.2 YAML Configuration with Reflection
- Move provider/tool configuration to YAML
- Implement reflection-based class loading
- Config versioning with auto-upgrade
- **Effort**: 3 days
- **Impact**: No code changes to swap providers

## Phase 4: Agent Capabilities (Priority: Medium)

### 4.1 Structured Clarification System
- 5 clarification types: missing_info, ambiguous, approach, risk, suggestion
- CLARIFY → PLAN → ACT workflow in system prompts
- Clean interrupt/resume via tool
- **Effort**: 2 days
- **Impact**: Better task understanding, fewer wasted cycles

### 4.2 Sandbox Execution
- Add Docker-based sandbox for code execution
- Virtual path system for isolation
- bash, read_file, write_file, str_replace tools
- **Effort**: 5 days
- **Impact**: Agents can actually execute code

### 4.3 File Upload & Conversion
- Accept PDF, PPT, Excel, Word uploads
- Auto-convert to Markdown (markitdown)
- Per-session file storage
- **Effort**: 3 days
- **Impact**: Rich document analysis

## Phase 5: Integration (Priority: Medium)

### 5.1 IM Channel Integration (Slack)
- Start with Slack (Socket Mode, no public IP)
- Message bus pattern for inbound/outbound
- Thread-based conversation tracking
- **Effort**: 3 days
- **Impact**: Team workflow integration

### 5.2 Telegram Integration
- Bot API with long-polling
- Simple setup (BotFather)
- Commands: /new, /status, /help
- **Effort**: 2 days
- **Impact**: Mobile access to agents

## Phase 6: Architecture Evolution (Priority: Low)

### 6.1 Harness/App Boundary
- Split core abstractions into publishable package
- Enforce import direction with CI test
- Enable library distribution
- **Effort**: 5 days
- **Impact**: Clean architecture, pip-installable core

### 6.2 Evaluate Agent Consolidation
- Compare 24-agent approach vs single-agent-with-skills
- Benchmark routing accuracy vs skill loading
- Consider hybrid: fewer specialized agents + rich skills
- **Effort**: 3 days (evaluation)
- **Impact**: Potentially simpler architecture

### 6.3 Memory Upload Filtering
- Strip file-upload references from persistent memory
- Prevent agents from searching for session-scoped files in future sessions
- **Effort**: 0.5 days
- **Impact**: Better cross-session memory accuracy

## Summary Timeline

| Phase | Priority | Effort | Key Deliverables |
|-------|----------|--------|-----------------|
| Phase 1 | Critical | 2.5 days | Loop detection, tool recovery, descriptions |
| Phase 2 | High | 4 days | Progressive skills, summarization |
| Phase 3 | High | 6 days | Embedded client, YAML config |
| Phase 4 | Medium | 10 days | Clarification, sandbox, uploads |
| Phase 5 | Medium | 5 days | Slack, Telegram |
| Phase 6 | Low | 8.5 days | Architecture evolution |

**Total**: ~36 days of estimated work, prioritized for maximum impact first.
