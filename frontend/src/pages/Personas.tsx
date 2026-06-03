import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import PersonaPopup from "@/components/PersonaPopup";
import { listPersonas, deletePersona } from "@/api/personas";
import { useAuth } from "@/hooks/useAuth";
import type { Persona } from "@/types";

export default function Personas() {
  const { token } = useAuth();
  const qc = useQueryClient();
  // null = popup closed; Persona = open with this initial (edit or copy depending on owner_id)
  // "new" sentinel = open empty (create from scratch)
  const [open, setOpen] = useState<Persona | "new" | null>(null);

  const { data: personas = [], isLoading, error } = useQuery({
    queryKey: ["personas"],
    queryFn: () => listPersonas(token!),
    enabled: !!token,
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deletePersona(token!, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["personas"] }),
    onError: (err) => console.error("Delete persona failed:", err),
  });

  if (isLoading) return <div className="p-6 text-muted-foreground">Loading personas…</div>;
  if (error) return <div className="p-6 text-destructive">Failed to load personas.</div>;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Personas</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Named system prompts. Pick one when configuring an agent.
          </p>
        </div>
        <Button onClick={() => setOpen("new")} className="bg-violet-600 hover:bg-violet-700 text-white">
          + New Persona
        </Button>
      </div>

      {personas.length === 0 ? (
        <div className="text-sm text-muted-foreground">No personas yet.</div>
      ) : (
        <div className="space-y-2">
          {personas.map((p) => {
            const isGlobal = p.owner_id === null;
            return (
              <div
                key={p.id}
                className="flex items-start gap-3 border rounded-xl p-4 hover:bg-violet-50/40 hover:border-violet-200 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-gray-900">{p.name}</span>
                    {isGlobal && (
                      <Badge variant="outline" className="text-[10px] font-normal text-gray-500 border-gray-300">
                        Default
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-3 whitespace-pre-wrap">
                    {p.system_prompt}
                  </p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {isGlobal ? (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-xs"
                        disabled
                        title="Defaults can't be edited — use Copy to make a variant you own."
                      >
                        Edit
                      </Button>
                      <Button variant="ghost" size="sm" className="text-xs" onClick={() => setOpen(p)}>
                        Copy
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-xs text-destructive hover:text-destructive"
                        disabled
                        title="Defaults can't be deleted."
                      >
                        Delete
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button variant="ghost" size="sm" className="text-xs" onClick={() => setOpen(p)}>
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-xs text-destructive hover:text-destructive"
                        onClick={() => {
                          if (confirm(`Delete persona "${p.name}"? This cannot be undone.`)) {
                            deleteMut.mutate(p.id);
                          }
                        }}
                      >
                        Delete
                      </Button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <PersonaPopup
        open={open !== null}
        initial={open === "new" ? null : open}
        onClose={() => setOpen(null)}
        onSaved={() => {}}
        onDeleted={() => {}}
      />
    </div>
  );
}
