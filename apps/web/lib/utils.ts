import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** tailwind-merge only knows Tailwind's stock scale. Tell it about the
 *  design system's own font sizes and shadow (app/globals.css) so that, for
 *  example, `text-label` is not mistaken for a text colour and dropped when
 *  merged with `text-foreground`. */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: ["display", "h1", "h2", "h3", "h4", "body", "reading", "label", "small", "code"],
        },
      ],
      shadow: [{ shadow: ["float"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
