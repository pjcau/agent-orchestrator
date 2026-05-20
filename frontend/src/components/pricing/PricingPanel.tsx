import { useState, useCallback } from "react";
import { usePricing } from "@/api/hooks";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/api/hooks";

/**
 * Model pricing browser panel.
 * Ported from vanilla app.js loadPricing / renderPricing (lines 2043-2095).
 */
export function PricingPanel() {
  const [search, setSearch] = useState("");
  const { data, isLoading, isError } = usePricing();
  const queryClient = useQueryClient();

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: queryKeys.pricing });
  }, [queryClient]);

  const allModels = data?.models ?? [];
  const q = search.toLowerCase();
  const filtered = q
    ? allModels.filter(
        (m) =>
          m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)
      )
    : allModels;
  const visible = filtered.slice(0, 50);

  return (
    <div className="pricing-panel">
      <div className="pricing-controls">
        <input
          className="pricing-search"
          type="search"
          placeholder="Filter models..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter pricing models"
        />
        <button
          className="btn-text"
          onClick={handleRefresh}
          disabled={isLoading}
        >
          Refresh
        </button>
      </div>

      {isLoading && <div className="empty-state">Loading...</div>}
      {isError && (
        <div className="empty-state">Failed to load pricing data</div>
      )}

      {!isLoading && !isError && (
        <>
          <div className="pricing-table">
            <div className="pricing-row pricing-header">
              <span className="pricing-model">Model</span>
              <span className="pricing-cost">In $/M</span>
              <span className="pricing-cost">Out $/M</span>
            </div>
            {visible.length === 0 ? (
              <div className="empty-state">No models found</div>
            ) : (
              visible.map((m) => (
                <div
                  key={m.id}
                  className={`pricing-row${m.is_free ? " pricing-free" : ""}`}
                >
                  <span className="pricing-model" title={m.id}>
                    {m.name}
                    {m.is_free && (
                      <span className="pricing-free-badge">free</span>
                    )}
                  </span>
                  <span className="pricing-cost">
                    {m.is_free ? "free" : `$${m.input_per_m.toFixed(2)}`}
                  </span>
                  <span className="pricing-cost">
                    {m.is_free ? "free" : `$${m.output_per_m.toFixed(2)}`}
                  </span>
                </div>
              ))
            )}
          </div>
          <div className="pricing-footer">
            {filtered.length} model{filtered.length !== 1 ? "s" : ""}
            {filtered.length > 50 ? " (showing 50)" : ""}
          </div>
        </>
      )}
    </div>
  );
}
