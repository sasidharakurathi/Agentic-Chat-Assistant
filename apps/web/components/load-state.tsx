"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError } from "@/lib/api";

/** Why a page could not load its main record. `reached` is false when the
 *  request never got an answer from the server (a network failure), which
 *  changes what the page tells the person to do. */
export type LoadFailure = { notFound: boolean; message: string; reached?: boolean };

export function loadFailure(err: unknown): LoadFailure {
  if (err instanceof ApiError) {
    return { notFound: err.status === 404, message: err.message, reached: true };
  }
  // Anything that isn't an ApiError never got a response from the API
  // (fetch rejects with a TypeError when the server can't be reached).
  return { notFound: false, message: "The server couldn't be reached.", reached: false };
}

/** Shown instead of the page when its record can't be loaded.
 *
 *  The API answers "not found" both for something that doesn't exist and
 *  for something in another org, on purpose, so as not to confirm it
 *  exists. The page says the same, rather than letting the rejection escape
 *  (which Next shows as a crash overlay in development). */
export function LoadFailed({ failure, what }: { failure: LoadFailure; what: string }) {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 md:px-8">
      <PageHeader
        title={failure.notFound ? `This ${what} can't be found` : `Couldn't load this ${what}`}
        description={
          failure.notFound
            ? "It doesn't exist, or it belongs to an organization you're not a member of. Check the organization selected in the menu."
            : failure.reached === false
              ? `${sentence(failure.message)} Check your connection, then reload the page.`
              : `${sentence(failure.message)} Reload the page, and if it keeps happening, try again later.`
        }
      />
      <Link href="/assistants" className={buttonVariants({ variant: "outline" })}>
        Back to assistants
      </Link>
    </div>
  );
}

/** End a server message with exactly one full stop. */
function sentence(message: string): string {
  const text = message.trim().replace(/[.!?]+$/, "");
  return text ? `${text}.` : "It didn't load.";
}
