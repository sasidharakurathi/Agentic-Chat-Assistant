import Link from "next/link";
import type { ReactNode } from "react";

import { BrandLockup } from "@/components/brand-mark";
import { DesktopOnlyNote } from "@/components/desktop-only";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center px-4 pt-[12vh] pb-12 sm:px-6 sm:pt-[22vh]">
      <div className="w-full max-w-[400px]">
        <Link
          href="/"
          className="focus-visible:ring-ring mb-6 inline-flex rounded-md focus-visible:ring-2 focus-visible:outline-none"
        >
          <BrandLockup size="lg" />
        </Link>
        {/* Signing in is for a computer: on a phone, the note instead of
            the form. */}
        <DesktopOnlyNote className="md:hidden" />
        <div className="hidden md:block">{children}</div>
      </div>
    </div>
  );
}
