export type SecretStatus = { name: string; status: string; required: boolean };
export type SetupState = { needs_admin: boolean; secrets: SecretStatus[] };

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) { window.location.href = "/login"; throw new Error("401"); }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}
