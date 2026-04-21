import { useState } from "react";
import {
  usePrompts,
  usePromptSearch,
  useCreatePrompt,
  useDeletePrompt,
} from "@/api/hooks";
import type { PromptTemplate } from "@/api/types";

/**
 * Left-panel prompt registry browser (PR #56).
 *
 * Lists every registered prompt, lets the user filter by tag/category,
 * preview content, create a new template, and delete one.
 */
export function PromptsPanel() {
  const { data: listData, isLoading, refetch } = usePrompts();
  const [tagFilter, setTagFilter] = useState<string>("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");

  const tags = tagFilter
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const searchEnabled = tags.length > 0 || Boolean(categoryFilter);
  const { data: searchData, isFetching: isSearching } = usePromptSearch(
    tags,
    categoryFilter || null
  );

  const templates: PromptTemplate[] = searchEnabled
    ? searchData?.templates ?? []
    : listData?.templates ?? [];

  const [selected, setSelected] = useState<PromptTemplate | null>(null);
  const deleteMutation = useDeletePrompt();

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete prompt "${name}"?`)) return;
    await deleteMutation.mutateAsync(name);
    if (selected?.name === name) setSelected(null);
  };

  return (
    <aside className="prompts-panel" aria-label="Prompt registry">
      <div className="prompts-panel__header">
        <h2>Prompts</h2>
        <button className="btn-text" onClick={() => refetch()}>
          Refresh
        </button>
      </div>

      <PromptCreateForm onCreated={() => refetch()} />

      <div className="prompts-panel__filters">
        <input
          placeholder="tags (comma separated)"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
        />
        <input
          placeholder="category"
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        />
      </div>

      {(isLoading || isSearching) && (
        <div className="empty-state">Loading…</div>
      )}

      <div className="prompts-list">
        {templates.length === 0 && !isLoading && !isSearching && (
          <div className="empty-state">
            {searchEnabled ? "No prompts match" : "No prompts registered"}
          </div>
        )}
        {templates.map((t) => (
          <div
            key={t.name}
            className={`prompts-item ${selected?.name === t.name ? "selected" : ""}`}
            onClick={() => setSelected(t)}
          >
            <div className="prompts-item__name">{t.name}</div>
            <div className="prompts-item__meta">
              {t.category && <span className="prompts-item__cat">{t.category}</span>}
              {t.tags.slice(0, 3).map((tag) => (
                <span key={tag} className="prompts-item__tag">
                  {tag}
                </span>
              ))}
              <span className="prompts-item__version">v{t.version}</span>
            </div>
            <button
              className="btn-session-delete"
              onClick={(e) => {
                e.stopPropagation();
                handleDelete(t.name);
              }}
              title="Delete prompt"
            >
              &times;
            </button>
          </div>
        ))}
      </div>

      {selected && (
        <div className="prompts-detail">
          <h3>{selected.name}</h3>
          {selected.description && <p className="prompts-detail__desc">{selected.description}</p>}
          <pre className="prompts-detail__content">{selected.content}</pre>
        </div>
      )}
    </aside>
  );
}

function PromptCreateForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const createMutation = useCreatePrompt();

  const reset = () => {
    setName("");
    setContent("");
    setTags("");
    setCategory("");
    setDescription("");
  };

  const submit = async () => {
    if (!name.trim() || !content.trim()) return;
    await createMutation.mutateAsync({
      name: name.trim(),
      content,
      tags: tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      category: category.trim() || null,
      description: description.trim() || null,
    });
    reset();
    setOpen(false);
    onCreated();
  };

  if (!open) {
    return (
      <button className="btn-primary prompts-new" onClick={() => setOpen(true)}>
        + New prompt
      </button>
    );
  }

  return (
    <div className="prompts-form">
      <input
        placeholder="name (unique)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        placeholder="category"
        value={category}
        onChange={(e) => setCategory(e.target.value)}
      />
      <input
        placeholder="tags (comma separated)"
        value={tags}
        onChange={(e) => setTags(e.target.value)}
      />
      <input
        placeholder="description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <textarea
        placeholder="Prompt content — supports {placeholders}"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={6}
      />
      <div className="prompts-form__actions">
        <button
          className="btn-primary"
          onClick={submit}
          disabled={createMutation.isPending || !name.trim() || !content.trim()}
        >
          {createMutation.isPending ? "Saving…" : "Save"}
        </button>
        <button className="btn-text" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {createMutation.isError && (
        <div className="prompts-form__error">
          {String(createMutation.error)}
        </div>
      )}
    </div>
  );
}
