import type { Notice } from "@/lib/api";
import { contactLink, noticeBlocks } from "@/lib/notice-text";

/** The notice's text, where messages go, and who to ask (Phase 8.7).
 *  Shared by the notice before the first message and Privacy & help. */
export function NoticeText({ notice }: { notice: Notice }) {
  const write = contactLink(notice.contact, "A question about my data in Chat");
  return (
    <div className="text-reading flex max-w-[68ch] flex-col gap-3">
      {noticeBlocks(notice.body).map((b, i) =>
        b.kind === "heading" ? (
          <h3 key={i} className="text-h4 mt-2 font-semibold">
            {b.text}
          </h3>
        ) : b.kind === "list" ? (
          <ul key={i} className="marker:text-muted-foreground list-disc space-y-1 pl-5">
            {b.items.map((item, j) => (
              <li key={j} className="pl-1">
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>{b.text}</p>
        ),
      )}
      <h3 className="text-h4 mt-2 font-semibold">How long</h3>
      <p>
        A conversation is erased for good {notice.retention_days ?? 365} days after its last
        message, unless it is kept for a legal reason. Removing it from your history doesn&apos;t
        erase it sooner.
      </p>
      <h3 className="text-h4 mt-2 font-semibold">Where your messages go</h3>
      <ul className="marker:text-muted-foreground list-disc space-y-1 pl-5">
        {notice.processors.map((p) => (
          <li key={p.name} className="pl-1">
            <span className="font-medium">{p.name}:</span> {p.what}
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground">
        What each assistant itself sends out is on its card (
        <span className="font-medium">About</span> in the conversation).
      </p>
      {notice.contact && (
        <p>
          Questions about your data:{" "}
          {write ? (
            <a
              href={write}
              className="text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2"
            >
              {notice.contact}
            </a>
          ) : (
            notice.contact
          )}
        </p>
      )}
    </div>
  );
}
