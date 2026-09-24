import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div
      className="flex min-h-screen items-center justify-center px-6 py-12"
      style={{
        backgroundImage:
          "radial-gradient(ellipse 80% 60% at 50% -10%, color-mix(in oklch, var(--primary) 12%, transparent), transparent)",
      }}
    >
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <span className="font-serif text-xl font-semibold tracking-tight">Assistant Studio</span>
        </div>
        {children}
      </div>
    </div>
  );
}
