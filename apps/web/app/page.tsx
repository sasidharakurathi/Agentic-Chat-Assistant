import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center gap-6 px-6 text-center">
      <p className="border-border text-muted-foreground rounded-full border px-3 py-1 text-xs">
        Phase 0 · foundations
      </p>
      <h1 className="text-4xl font-semibold tracking-tight">Assistant Studio</h1>
      <p className="text-muted-foreground max-w-xl">
        Build your own agentic chat assistant: wire up knowledge bases, databases, tools and MCP
        servers on a visual canvas, then chat with it.
      </p>
      <div className="flex gap-3">
        <Link href="/register" className={cn(buttonVariants())}>
          Get started
        </Link>
        <Link href="/login" className={cn(buttonVariants({ variant: "outline" }))}>
          Sign in
        </Link>
      </div>
    </main>
  );
}
