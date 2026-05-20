import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkspaceFilePicker } from "@/components/files/WorkspaceFilePicker";

vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    defaults: { headers: {} },
  },
}));

import apiClient from "@/api/client";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const ROOT_DATA = {
  items: [
    { name: "src", path: "src", size: 0, is_dir: true },
    { name: "README.md", path: "README.md", size: 1024, is_dir: false },
  ],
};

const SRC_DATA = {
  items: [
    { name: "main.py", path: "src/main.py", size: 512, is_dir: false },
  ],
};

const FILE_DATA = { path: "README.md", content: "# Hello world" };

describe("WorkspaceFilePicker", () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockImplementation(async (url: string) => {
      if ((url as string).includes("/api/file?")) return { data: FILE_DATA };
      if ((url as string).includes("path=src")) return { data: SRC_DATA };
      // root path or any /api/files call
      return { data: ROOT_DATA };
    });
  });

  it("renders root directory listing when opened", async () => {
    render(
      <WorkspaceFilePicker open={true} onClose={vi.fn()} onPick={vi.fn()} />,
      { wrapper }
    );
    await waitFor(() =>
      expect(screen.getByText("src")).toBeInTheDocument()
    );
    expect(screen.getByText("README.md")).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    render(
      <WorkspaceFilePicker open={false} onClose={vi.fn()} onPick={vi.fn()} />,
      { wrapper }
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("navigates into a directory on click", async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceFilePicker open={true} onClose={vi.fn()} onPick={vi.fn()} />,
      { wrapper }
    );
    await waitFor(() =>
      expect(screen.getByText("src")).toBeInTheDocument()
    );
    await user.click(screen.getByText("src"));
    await waitFor(() =>
      expect(screen.getByText("main.py")).toBeInTheDocument()
    );
  });

  it("calls onPick with file content and closes when a file is clicked", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(
      <WorkspaceFilePicker open={true} onClose={onClose} onPick={onPick} />,
      { wrapper }
    );
    await waitFor(() =>
      expect(screen.getByText("README.md")).toBeInTheDocument()
    );
    await user.click(screen.getByText("README.md"));
    await waitFor(() => expect(onPick).toHaveBeenCalledTimes(1));
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "README.md",
        content: "# Hello world",
      })
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("renders breadcrumb buttons and navigates back to root", async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceFilePicker open={true} onClose={vi.fn()} onPick={vi.fn()} />,
      { wrapper }
    );
    await waitFor(() =>
      expect(screen.getByText("src")).toBeInTheDocument()
    );
    // Navigate into src
    await user.click(screen.getByText("src"));
    await waitFor(() =>
      expect(screen.getByText("main.py")).toBeInTheDocument()
    );
    // Click the "root" breadcrumb to go back
    await user.click(screen.getByRole("button", { name: "root" }));
    await waitFor(() =>
      expect(screen.getByText("README.md")).toBeInTheDocument()
    );
  });
});
