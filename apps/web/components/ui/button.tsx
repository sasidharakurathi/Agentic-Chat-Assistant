import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const buttonStyles = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-colors duration-120 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:bg-primary-hover active:bg-primary-active",
        outline: "border border-field-border bg-card text-foreground hover:bg-muted",
        ghost: "text-foreground hover:bg-muted",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive-hover active:bg-destructive-active",
        link: "text-foreground underline decoration-1 underline-offset-[3px] hover:decoration-2",
      },
      size: {
        default: "h-9 px-4 text-sm",
        sm: "h-8 px-3 text-label",
        lg: "h-11 px-6 text-sm",
        icon: "size-8 p-0 text-sm",
      },
    },
    compoundVariants: [
      // A link reads as text in running copy: no box height or padding.
      { variant: "link", size: ["default", "sm", "lg"], className: "h-auto px-0" },
    ],
    defaultVariants: { variant: "default", size: "default" },
  },
);

type ButtonVariantProps = VariantProps<typeof buttonStyles>;

/** Button classes (docs/DESIGN.md section 5), for a `<Link>` or a raw
 *  `<button>` that should look like a Button. Radius 6, 16px lucide icons
 *  with an 8px gap, no arrows appended, a 2px ink focus ring with a 2px
 *  offset, no press-scale. `size: "icon"` needs an `aria-label`. The result
 *  is already merged, so it is safe to use without `cn()`. */
export function buttonVariants({
  className,
  ...variants
}: ButtonVariantProps & { className?: string } = {}): string {
  return cn(buttonStyles(variants), className);
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & ButtonVariantProps;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonStyles({ variant, size }), className)} {...props} />;
}
