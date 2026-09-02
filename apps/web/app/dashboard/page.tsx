"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, auth, orgs, tokenStore, type Org } from "@/lib/api";

type Me = Awaited<ReturnType<typeof auth.me>>;

export default function DashboardPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [orgList, setOrgList] = useState<Org[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tokenStore.access) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        const [meRes, orgRes] = await Promise.all([auth.me(), orgs.list()]);
        setMe(meRes);
        setOrgList(orgRes);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          tokenStore.clear();
          router.replace("/login");
          return;
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  function signOut() {
    const refresh = tokenStore.refresh;
    if (refresh) void auth.logout(refresh).catch(() => {});
    tokenStore.clear();
    router.replace("/login");
  }

  if (loading) {
    return <div className="text-muted-foreground p-10 text-sm">Loading…</div>;
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground text-sm">Signed in as {me?.user.email}</p>
        </div>
        <Button variant="outline" size="sm" onClick={signOut}>
          Sign out
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Your organizations</CardTitle>
          <CardDescription>
            Assistants live inside an organization. The assistant builder arrives in Phase 1.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {orgList.map((o) => (
            <div
              key={o.id}
              className="border-border flex items-center justify-between rounded-md border px-4 py-3 text-sm"
            >
              <span className="font-medium">{o.name}</span>
              <span className="text-muted-foreground text-xs">
                {o.is_personal ? "personal" : o.slug}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="border-border text-muted-foreground mt-8 rounded-lg border border-dashed p-10 text-center text-sm">
        No assistants yet — the canvas builder ships in Phase 1.
      </div>
    </main>
  );
}
