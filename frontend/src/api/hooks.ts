import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import apiClient from "./client";
import type {
  ModelsResponse,
  AgentsResponse,
  SessionInfo,
  SessionHistoryResponse,
  JobsListResponse,
  UsageStats,
  CacheStatsResponse,
  MCPToolsResponse,
  PromptRequest,
  PromptResponse,
  AgentRunRequest,
  AgentRunResponse,
  TeamRunRequest,
  TeamRunResponse,
  ConversationNewResponse,
  JobRecord,
  SandboxStatus,
  SandboxInfo,
  PromptListResponse,
  PromptTemplate,
  CompactionStats,
} from "./types";

// Query keys — centralised for cache invalidation
export const queryKeys = {
  models: ["models"] as const,
  agents: ["agents"] as const,
  session: ["session"] as const,
  sessionHistory: ["session", "history"] as const,
  jobsList: ["jobs", "list"] as const,
  jobDetail: (sessionId: string) => ["jobs", sessionId] as const,
  usage: ["usage"] as const,
  cacheStats: ["cache", "stats"] as const,
  mcpTools: ["mcp", "tools"] as const,
  sandboxStatus: ["sandbox", "status"] as const,
  sandboxInfo: (sessionId: string) => ["sandbox", sessionId, "info"] as const,
  prompts: ["prompts", "list"] as const,
  promptSearch: (tags: string[], category: string | null) =>
    ["prompts", "search", tags.join(","), category ?? ""] as const,
  compactionStats: ["compaction", "stats"] as const,
};

// --- Queries ---

export function useModels(
  options?: Partial<UseQueryOptions<ModelsResponse>>
) {
  return useQuery<ModelsResponse>({
    queryKey: queryKeys.models,
    queryFn: async () => {
      const resp = await apiClient.get<ModelsResponse>("/api/models");
      return resp.data;
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
    ...options,
  });
}

export function useAgents(
  options?: Partial<UseQueryOptions<AgentsResponse>>
) {
  return useQuery<AgentsResponse>({
    queryKey: queryKeys.agents,
    queryFn: async () => {
      const resp = await apiClient.get<AgentsResponse>("/api/agents");
      return resp.data;
    },
    staleTime: 10 * 60 * 1000, // 10 minutes
    ...options,
  });
}

export function useSessionInfo(
  options?: Partial<UseQueryOptions<SessionInfo>>
) {
  return useQuery<SessionInfo>({
    queryKey: queryKeys.session,
    queryFn: async () => {
      const resp = await apiClient.get<SessionInfo>("/api/session");
      return resp.data;
    },
    staleTime: 60 * 1000,
    ...options,
  });
}

export function useSessionHistory(
  options?: Partial<UseQueryOptions<SessionHistoryResponse>>
) {
  return useQuery<SessionHistoryResponse>({
    queryKey: queryKeys.sessionHistory,
    queryFn: async () => {
      const resp = await apiClient.get<SessionHistoryResponse>("/api/session/history");
      return resp.data;
    },
    staleTime: 30 * 1000,
    ...options,
  });
}

export function useJobsList(
  options?: Partial<UseQueryOptions<JobsListResponse>>
) {
  return useQuery<JobsListResponse>({
    queryKey: queryKeys.jobsList,
    queryFn: async () => {
      const resp = await apiClient.get<JobsListResponse>("/api/jobs/list");
      return resp.data;
    },
    staleTime: 15 * 1000,
    ...options,
  });
}

export function useJobDetail(
  sessionId: string,
  options?: Partial<UseQueryOptions<{ records: JobRecord[] }>>
) {
  return useQuery<{ records: JobRecord[] }>({
    queryKey: queryKeys.jobDetail(sessionId),
    queryFn: async () => {
      const resp = await apiClient.get<{ records: JobRecord[] }>(
        `/api/jobs/${encodeURIComponent(sessionId)}`
      );
      return resp.data;
    },
    enabled: Boolean(sessionId),
    staleTime: 30 * 1000,
    ...options,
  });
}

export function useUsage(
  options?: Partial<UseQueryOptions<UsageStats>>
) {
  return useQuery<UsageStats>({
    queryKey: queryKeys.usage,
    queryFn: async () => {
      const resp = await apiClient.get<UsageStats>("/api/usage");
      return resp.data;
    },
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000, // Auto-refresh every minute
    ...options,
  });
}

export function useCacheStats(
  options?: Partial<UseQueryOptions<CacheStatsResponse>>
) {
  return useQuery<CacheStatsResponse>({
    queryKey: queryKeys.cacheStats,
    queryFn: async () => {
      const resp = await apiClient.get<CacheStatsResponse>("/api/cache/stats");
      return resp.data;
    },
    staleTime: 10 * 1000,
    refetchInterval: 30 * 1000,
    ...options,
  });
}

export function useMCPTools(
  options?: Partial<UseQueryOptions<MCPToolsResponse>>
) {
  return useQuery<MCPToolsResponse>({
    queryKey: queryKeys.mcpTools,
    queryFn: async () => {
      const resp = await apiClient.get<MCPToolsResponse>("/api/mcp/tools");
      return resp.data;
    },
    staleTime: 5 * 60 * 1000,
    ...options,
  });
}

export function useSandboxStatus(
  options?: Partial<UseQueryOptions<SandboxStatus>>
) {
  return useQuery<SandboxStatus>({
    queryKey: queryKeys.sandboxStatus,
    queryFn: async () => {
      const resp = await apiClient.get<SandboxStatus>("/api/sandbox/status");
      return resp.data;
    },
    staleTime: 10 * 1000,
    refetchInterval: 15 * 1000,
    ...options,
  });
}

export function useSandboxInfo(
  sessionId: string,
  options?: Partial<UseQueryOptions<SandboxInfo>>
) {
  return useQuery<SandboxInfo>({
    queryKey: queryKeys.sandboxInfo(sessionId),
    queryFn: async () => {
      const resp = await apiClient.get<SandboxInfo>(
        `/api/sandbox/${encodeURIComponent(sessionId)}/info`
      );
      return resp.data;
    },
    enabled: Boolean(sessionId),
    staleTime: 5 * 1000,
    refetchInterval: 10 * 1000,
    ...options,
  });
}

// ── Prompt registry (PR #56) ───────────────────────────────────────────

export function usePrompts(
  options?: Partial<UseQueryOptions<PromptListResponse>>
) {
  return useQuery<PromptListResponse>({
    queryKey: queryKeys.prompts,
    queryFn: async () => {
      const resp = await apiClient.get<PromptListResponse>("/api/prompts");
      return resp.data;
    },
    staleTime: 30 * 1000,
    ...options,
  });
}

export function usePromptSearch(
  tags: string[],
  category: string | null,
  options?: Partial<UseQueryOptions<PromptListResponse>>
) {
  const enabled = tags.length > 0 || Boolean(category);
  return useQuery<PromptListResponse>({
    queryKey: queryKeys.promptSearch(tags, category),
    queryFn: async () => {
      const params = new URLSearchParams();
      if (tags.length > 0) params.set("tags", tags.join(","));
      if (category) params.set("category", category);
      const resp = await apiClient.get<PromptListResponse>(
        `/api/prompts/search?${params.toString()}`
      );
      return resp.data;
    },
    enabled,
    staleTime: 15 * 1000,
    ...options,
  });
}

export function useCreatePrompt() {
  const qc = useQueryClient();
  return useMutation<PromptTemplate, Error, Partial<PromptTemplate> & { name: string; content: string }>(
    {
      mutationFn: async (body) => {
        const resp = await apiClient.post<PromptTemplate>("/api/prompts", body);
        return resp.data;
      },
      onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.prompts }),
    }
  );
}

export function useDeletePrompt() {
  const qc = useQueryClient();
  return useMutation<{ deleted: string }, Error, string>({
    mutationFn: async (name) => {
      const resp = await apiClient.delete<{ deleted: string }>(
        `/api/prompts/${encodeURIComponent(name)}`
      );
      return resp.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.prompts }),
  });
}

// ── Compaction stats (PR #60) ──────────────────────────────────────────

export function useCompactionStats(
  options?: Partial<UseQueryOptions<CompactionStats>>
) {
  return useQuery<CompactionStats>({
    queryKey: queryKeys.compactionStats,
    queryFn: async () => {
      const resp = await apiClient.get<CompactionStats>(
        "/api/compaction/stats"
      );
      return resp.data;
    },
    staleTime: 10 * 1000,
    refetchInterval: 20 * 1000,
    ...options,
  });
}

// --- Mutations ---

export function usePrompt() {
  const queryClient = useQueryClient();
  return useMutation<PromptResponse, Error, PromptRequest>({
    mutationFn: async (req) => {
      const resp = await apiClient.post<PromptResponse>("/api/prompt", req);
      return resp.data;
    },
    onSuccess: () => {
      // Refresh usage stats after each request
      queryClient.invalidateQueries({ queryKey: queryKeys.usage });
    },
  });
}

export function useAgentRun() {
  const queryClient = useQueryClient();
  return useMutation<AgentRunResponse, Error, AgentRunRequest>({
    mutationFn: async (req) => {
      const resp = await apiClient.post<AgentRunResponse>("/api/agent/run", req);
      return resp.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.usage });
    },
  });
}

export function useTeamRun() {
  const queryClient = useQueryClient();
  return useMutation<TeamRunResponse, Error, TeamRunRequest>({
    mutationFn: async (req) => {
      const resp = await apiClient.post<TeamRunResponse>("/api/team/run", req);
      return resp.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.usage });
      queryClient.invalidateQueries({ queryKey: queryKeys.jobsList });
    },
  });
}

export function useDeleteJob() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (sessionId) => {
      await apiClient.delete(`/api/jobs/${encodeURIComponent(sessionId)}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.jobsList });
    },
  });
}

export function useNewConversation() {
  return useMutation<ConversationNewResponse, Error, void>({
    mutationFn: async () => {
      const resp = await apiClient.post<ConversationNewResponse>("/api/conversation/new");
      return resp.data;
    },
  });
}

export function useClearCache() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await apiClient.post("/api/cache/clear");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cacheStats });
    },
  });
}

export function useResumeRun() {
  return useMutation<void, Error, { runId: string; value: string }>({
    mutationFn: async ({ runId, value }) => {
      await apiClient.post(`/api/runs/${encodeURIComponent(runId)}/resume`, { value });
    },
  });
}

export function useGraphReset() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await apiClient.post("/api/graph/reset");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.usage });
    },
  });
}

export function useSandboxCleanup() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (sessionId) => {
      await apiClient.delete(`/api/sandbox/${encodeURIComponent(sessionId)}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sandboxStatus });
    },
  });
}
