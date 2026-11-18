import { useEffect, useState } from "react";
import { api, apiErrorMessage } from "../api";

export type PlatformVariant = {
  id: string;
  label: string;
  selection: Record<string, string>;
  props: Record<string, unknown>;
  width: number;
  height: number;
};

export type PlatformComponent = {
  name: string;
  category?: string | null;
  import_path: string;
  description: string;
  story_id?: string | null;
  variants: PlatformVariant[];
  properties: { name: string; type: "text"; default: string; prop: string }[];
  limitations: string[];
};

export type PlatformLibrary = {
  id: "agent-platform";
  revision: number;
  components: PlatformComponent[];
};

export type PlatformImplementation = {
  status: "exact" | "detached";
  component: string;
  import_path: string;
  revision: number;
  variant?: string | null;
  props?: Record<string, unknown>;
  snippet?: string | null;
  story_id?: string | null;
  limitations?: string[];
};

export function usePlatformLibrary(enabled: boolean) {
  const [library, setLibrary] = useState<PlatformLibrary | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled || library) return;
    const controller = new AbortController();
    setError("");
    api<PlatformLibrary>("/api/ui-designer/component-library", { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setLibrary(result);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(apiErrorMessage(error, "Could not load Agent Platform components."));
      });
    return () => controller.abort();
  }, [enabled, library, attempt]);

  return { library, error, reload: () => { setError(""); setAttempt((n) => n + 1); } };
}
