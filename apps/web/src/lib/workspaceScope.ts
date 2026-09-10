import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

export type WorkspaceScope = "all" | "work" | "personal";

export interface WorkspaceCounts {
  all: number;
  work: number;
  personal: number;
}

export function normalizeWorkspaceScope(value: string | null): WorkspaceScope {
  return value === "work" || value === "personal" ? value : "all";
}

export function workspacePath(path: string, scope: WorkspaceScope): string {
  const [pathname = path, search = ""] = path.split("?");
  const params = new URLSearchParams(search);
  if (scope === "all") {
    params.delete("workspace");
  } else {
    params.set("workspace", scope);
  }
  const suffix = params.toString();
  return suffix ? `${pathname}?${suffix}` : pathname;
}

export function useWorkspaceScope() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("workspace");
  const scope = normalizeWorkspaceScope(raw);

  useEffect(() => {
    if (raw === null || raw === scope) return;
    const canonical = new URLSearchParams(params);
    canonical.set("workspace", "all");
    canonical.delete("page");
    setParams(canonical, { replace: true });
  }, [params, raw, scope, setParams]);

  function setScope(next: WorkspaceScope) {
    const updated = new URLSearchParams(params);
    updated.set("workspace", next);
    updated.delete("page");
    setParams(updated);
  }

  return { scope, setScope };
}
