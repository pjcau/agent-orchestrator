import { useAppStore } from "@/stores/useAppStore";

const STATUS_ICONS: Record<string, string> = {
  pending: "·",
  in_progress: "~",
  completed: "✓",
  failed: "✗",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  in_progress: "Running",
  completed: "Done",
  failed: "Failed",
};

/** Task plan sidebar section showing node execution status with timing. */
export function TaskPlan() {
  const { taskPlanItems } = useAppStore();

  if (taskPlanItems.length === 0) {
    return (
      <div className="task-plan task-plan--empty">
        <span className="empty-state">No active plan</span>
      </div>
    );
  }

  return (
    <div className="task-plan">
      {taskPlanItems.map((item) => (
        <div
          key={item.nodeId}
          className={`task-plan-item task-plan-item--${item.status}`}
          aria-label={`${item.nodeId}: ${STATUS_LABELS[item.status]}`}
        >
          <span
            className={`task-plan-icon task-plan-icon--${item.status}`}
            aria-hidden="true"
          >
            {STATUS_ICONS[item.status] ?? "·"}
          </span>
          <span className="task-plan-name">{item.nodeId}</span>
          {item.elapsed !== null && item.elapsed !== undefined && (
            <span className="task-plan-elapsed">{item.elapsed}s</span>
          )}
        </div>
      ))}
    </div>
  );
}
