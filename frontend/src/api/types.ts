// API types matching the Agent Orchestrator backend

export interface PromptRequest {
  prompt: string;
  model: string;
  provider: string;
  graph_type?: string;
  conversation_id?: string | null;
  file_context?: string;
}

export interface PromptResponse {
  success: boolean;
  output?: string;
  error?: string;
  steps?: Array<{ node: string; output: string }>;
  agent_costs?: Record<string, AgentCost>;
  usage?: UsageInfo;
  elapsed_s?: number;
}

export interface AgentRunRequest {
  agent: string;
  task: string;
  model: string;
  provider: string;
  tools?: string[];
  max_steps?: number;
  conversation_id?: string | null;
}

export interface AgentRunResponse {
  success: boolean;
  output?: string;
  error?: string;
  status?: string;
  total_tokens?: number;
  total_cost_usd?: number;
  elapsed_s?: number;
}

export interface TeamRunRequest {
  task: string;
  model: string;
  provider?: string;
  conversation_id?: string | null;
}

export interface TeamRunResponse {
  job_id: string;
  status: "started" | "running" | "completed" | "failed";
  result?: TeamRunResult;
  error?: string;
}

export interface TeamRunResult {
  success: boolean;
  output?: string;
  error?: string;
  plan?: string;
  agent_outputs?: Record<string, string>;
  agent_costs?: Record<string, AgentCost>;
  total_tokens?: number;
  total_cost_usd?: number;
  elapsed_s?: number;
  fallback_log?: Array<{
    agent: string;
    model: string;
    status: string;
    detail: string;
  }>;
}

export interface AgentCost {
  tokens?: number;
  cost_usd?: number;
}

export interface UsageInfo {
  input_tokens?: number;
  output_tokens?: number;
  model?: string;
  provider?: string;
}

export interface UsageStats {
  total_tokens: number;
  total_cost_usd: number;
  avg_speed: number;
  total_requests: number;
  session_speed?: number;
  db_connected: boolean;
}

export interface SessionInfo {
  session_id: string;
  started_at: string;
  working_directory: string;
}

export interface ModelInfo {
  name: string;
  size: string;
}

export interface ModelsResponse {
  ollama: ModelInfo[];
  openrouter: ModelInfo[];
}

export interface AgentInfo {
  name: string;
  description: string;
  category: string;
  provider: string;
  role?: string;
}

export interface AgentsResponse {
  agents: AgentInfo[];
  categories: Record<string, AgentInfo[]>;
}

export interface RunInfo {
  run_id: string;
  status: "pending" | "running" | "interrupted" | "completed" | "failed";
  result?: unknown;
  error?: string;
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string | AssistantContent;
  model?: string;
  provider?: string;
  timestamp?: number;
}

export interface AssistantContent {
  steps?: Array<{ node: string; output: string }>;
  agent_costs?: Record<string, AgentCost>;
  usage?: UsageInfo;
  elapsed_s?: number;
  output?: string;
}

export interface GraphData {
  nodes: string[];
  edges: Array<{
    source: string;
    target?: string;
    routes?: string[];
    type?: string;
  }>;
}

export interface CacheStats {
  hits: number;
  misses: number;
  hit_rate: number;
  evictions: number;
  entries?: number;
  total_saved_tokens?: number;
}

export interface Snapshot {
  orchestrator_status: string;
  agents: Record<string, AgentSnapshot>;
  tasks: TaskSnapshot[];
  total_cost_usd: number;
  total_tokens: number;
  graph: GraphData;
  cache: CacheStats;
  event_count: number;
}

export interface AgentSnapshot {
  name?: string;
  status: string;
  current_task?: string;
  steps?: number;
  tokens?: number;
  cost_usd?: number;
  provider?: string;
  role?: string;
  tools?: string[];
}

export interface TaskSnapshot {
  task_id?: string;
  from_agent?: string;
  to_agent?: string;
  description?: string;
  status: string;
  priority?: string;
}

export interface WSEvent {
  type: "snapshot" | "event";
  data: Snapshot | OrchestratorEvent;
}

export interface OrchestratorEvent {
  event_type: string;
  agent_name?: string;
  node_name?: string;
  timestamp: number;
  data: Record<string, unknown>;
}

export interface StreamToken {
  type: "start" | "token" | "done" | "error";
  content?: string;
  output?: string;
  usage?: UsageInfo;
  elapsed_s?: number;
  speed?: number;
  error?: string;
}

export interface JobSession {
  session_id: string;
  first_prompt?: string;
  records: number;
  files: number;
  is_current: boolean;
}

export interface JobRecord {
  job_number: number;
  job_type: string;
  prompt?: string;
  task?: string;
  agent?: string;
  model?: string;
  result?: {
    success?: boolean;
    output?: string;
    error?: string;
    total_tokens?: number;
    tokens?: number;
    total_cost_usd?: number;
    elapsed_s?: number;
    agent_costs?: Record<string, AgentCost>;
  };
}

export interface MCPTool {
  name: string;
  description: string;
  input_schema?: Record<string, unknown>;
}

export interface MCPToolsResponse {
  tools: MCPTool[];
  count: number;
}

export interface ConversationNewResponse {
  conversation_id: string;
}

export interface SessionHistoryResponse {
  records: JobRecord[];
}

export interface JobsListResponse {
  sessions: JobSession[];
}

export interface CacheStatsResponse {
  cache: CacheStats;
}

export interface PresetsResponse {
  presets: Array<{
    label: string;
    prompt: string;
    graph: string;
    icon: string;
  }>;
}

export interface FileItem {
  name: string;
  path: string;
  size: number;
  is_dir: boolean;
}

export interface FilesResponse {
  items: FileItem[];
}

export interface FileContentResponse {
  path: string;
  content: string;
  error?: string;
}

export interface PricingModel {
  id: string;
  name: string;
  input_per_m: number;
  output_per_m: number;
  is_free: boolean;
}

export interface PricingResponse {
  models: PricingModel[];
}

export interface TaskPlanItem {
  nodeId: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  startedAt: number;
  elapsed: number | null;
}

export interface InteractionItem {
  from: string;
  to: string;
  desc: string;
  status: "pending" | "running" | "completed" | "failed";
  time: number;
}
