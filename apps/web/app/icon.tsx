import { ImageResponse } from "next/og";

import { AppIconArt } from "@/components/app-icon";

/** The browser tab's icon and the installable app's (Phase 8.6): served at
 *  /icon/192 and /icon/512, which the manifest names. */
export function generateImageMetadata() {
  return [192, 512].map((n) => ({
    id: String(n),
    size: { width: n, height: n },
    contentType: "image/png",
  }));
}

export default function Icon({ id }: { id: string }) {
  const size = Number(id) || 192;
  return new ImageResponse(<AppIconArt size={size} />, { width: size, height: size });
}
