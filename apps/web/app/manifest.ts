import type { MetadataRoute } from "next";

/** The installable app (Phase 8.6, plan D12): Chat on a phone's home
 *  screen. It opens on Chat; the Studio is still for a computer. The
 *  colours are the light theme's paper and ink (docs/DESIGN.md). */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Assistant Studio Chat",
    short_name: "Chat",
    description: "Ask your company's assistants.",
    start_url: "/chat",
    scope: "/",
    display: "standalone",
    background_color: "#f3f6f4",
    theme_color: "#f3f6f4",
    icons: [
      { src: "/icon/192", sizes: "192x192", type: "image/png" },
      { src: "/icon/512", sizes: "512x512", type: "image/png" },
      { src: "/icon/512", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
