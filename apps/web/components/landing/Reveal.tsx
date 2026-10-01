"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Holds a block of the landing page back until it scrolls into view, then
 *  lets its parts come in (the .rv classes in globals.css). A block already
 *  on screen when the page opens is left as it is, and so is everything
 *  without JavaScript or under reduced motion: nothing is hidden that would
 *  not then appear. */
export function Reveal({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"still" | "wait" | "in">("still");

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    if (el.getBoundingClientRect().top < window.innerHeight * 0.9) return;
    setState("wait");
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        setState("in");
        io.disconnect();
      },
      { rootMargin: "0px 0px -12% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={cn(state === "wait" && "rv-wait", state === "in" && "rv-in", className)}
    >
      {children}
    </div>
  );
}
