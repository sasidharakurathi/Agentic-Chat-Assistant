import { ImageResponse } from "next/og";

import { AppIconArt } from "@/components/app-icon";

/** The iPhone home-screen icon (Phase 8.6): iOS reads the apple-touch-icon,
 *  not the manifest's. */
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(<AppIconArt size={180} />, size);
}
