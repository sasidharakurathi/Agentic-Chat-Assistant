/** The app's icon (Phase 8.6): the brand mark (components/brand-mark.tsx)
 *  in paper on ink, drawn for `next/og` at any size. The mark sits in the
 *  middle half, inside the safe zone a phone crops a maskable icon to. */
export function AppIconArt({ size }: { size: number }) {
  const mark = Math.round(size * 0.56);
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#1c2a25",
      }}
    >
      <svg
        width={mark}
        height={Math.round((mark * 18) / 32)}
        viewBox="0 0 32 18"
        fill="none"
        stroke="#f3f6f4"
      >
        <path d="M1 9h20" strokeWidth={3} />
        <path d="M7 4v10M14 4v10" strokeWidth={2.25} />
        <circle cx={25.5} cy={9} r={5} strokeWidth={3} />
      </svg>
    </div>
  );
}
