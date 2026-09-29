"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { ApiError } from "@/lib/api";

/** Why a page could not load its main record. */
export type LoadFailure = { notFound: boolean; message: string };

export function loadFailure(err: unknown): LoadFailure {
  if (err instanceof ApiError) {
    return { notFound: err.status === 404, message: err.message };
  }
  return { notFound: false, message: err instanceof Error ? err.message : "Could not load" };
}

/** Shown instead of the page when its record can't be loaded.
 *
 *  The API answers "not found" both for something that doesn't exist and
 *  for something in another org, on purpose, so as not to confirm it
 *  exists. The page says the same, rather than letting the rejection escape
 *  (which Next shows as a crash overlay in development). */
export function LoadFailed({ failure, what }: { failure: LoadFailure; what: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-10 text-center">
      <h1 className="font-serif text-xl">
        {failure.notFound ? `This ${what} can't be found` : `Couldn't load this ${what}`}
      </h1>
      <p className="text-muted-foreground max-w-md text-sm">
        {failure.notFound
          ? `It doesn't exist, or it belongs to an organization you're not a member of. Check the org selected in the sidebar.`
          : failure.message}
      </p>
      <Link href="/assistants" className={buttonVariants({ variant: "outline" })}>
        Back to assistants
      </Link>
    </div>
  );
}
