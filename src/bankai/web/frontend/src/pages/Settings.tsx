import { useEffect, useState } from "react";
import { Settings as SettingsIcon, Save, Loader2, Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";
import { api, type SettingRow } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function label(key: string) {
  return key
    .split(/[._]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export default function Settings() {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const r = await api.settings();
      setRows(r.settings);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function save(key: string, value: any) {
    setSaving(key);
    try {
      await api.setSetting(key, value);
      toast.success(`${label(key)} saved`);
      setEdits((e) => {
        const n = { ...e };
        delete n[key];
        return n;
      });
      await load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Edit safe configuration keys.</p>
      </header>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map((row) => {
            const isBool = typeof row.value === "boolean";
            const current = key(edits, row);
            return (
              <Card key={row.key}>
                <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{label(row.key)}</span>
                      {row.secret &&
                        (row.is_set ? (
                          <Badge variant="success">Set</Badge>
                        ) : (
                          <Badge variant="muted">Unset</Badge>
                        ))}
                    </div>
                    <code className="text-xs text-muted-foreground">{row.key}</code>
                  </div>

                  <div className="flex items-center gap-2">
                    {isBool ? (
                      <Switch
                        checked={Boolean(current)}
                        onCheckedChange={(v) => save(row.key, v)}
                      />
                    ) : (
                      <>
                        <div className="relative">
                          <Input
                            type={row.secret && !reveal[row.key] ? "password" : "text"}
                            value={current ?? ""}
                            placeholder={row.secret && row.is_set ? "•••••• (set)" : ""}
                            onChange={(e) =>
                              setEdits((ed) => ({ ...ed, [row.key]: e.target.value }))
                            }
                            className="w-64 pr-9"
                          />
                          {row.secret && (
                            <button
                              type="button"
                              onClick={() => setReveal((r) => ({ ...r, [row.key]: !r[row.key] }))}
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                            >
                              {reveal[row.key] ? (
                                <EyeOff className="h-4 w-4" />
                              ) : (
                                <Eye className="h-4 w-4" />
                              )}
                            </button>
                          )}
                        </div>
                        <Button
                          size="sm"
                          onClick={() => save(row.key, edits[row.key])}
                          disabled={saving === row.key || edits[row.key] === undefined}
                        >
                          {saving === row.key ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Save className="h-4 w-4" />
                          )}
                        </Button>
                      </>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
          {rows.length === 0 && (
            <div className="flex flex-col items-center py-16 text-muted-foreground">
              <SettingsIcon className="mb-3 h-8 w-8" />
              No editable settings.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function key(edits: Record<string, any>, row: SettingRow) {
  if (row.key in edits) return edits[row.key];
  if (row.secret) return "";
  return row.value;
}
