import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** The single sign-in plate. Below 480px it drops its border and fills
 *  the width, so the form isn't a box inside a small screen. */
export function AuthCard({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Card
      className={cn(
        "max-[479px]:border-0 max-[479px]:bg-transparent max-[479px]:[&>div]:px-0",
        className,
      )}
    >
      {children}
    </Card>
  );
}
