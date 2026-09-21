import { useLayoutEffect, useRef } from "react";

/** Open at the latest exchange; follow growth until the reader scrolls away. */
export function useChatScroll(conversation: string) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const previousConversation = useRef(conversation);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const bottom = () => {
    const el = viewport.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  };
  useLayoutEffect(() => {
    if (previousConversation.current !== conversation) {
      previousConversation.current = conversation;
      following.current = true;
      prepend.current = null;
    }
    const el = viewport.current;
    if (el && prepend.current) {
      el.scrollTop =
        prepend.current.top + el.scrollHeight - prepend.current.height;
      prepend.current = null;
    } else bottom();
    // Images, fonts, expanding tool details and viewport resizing can change the
    // height after React commits. Never scroll the surrounding page or composer.
    const observer = new ResizeObserver(bottom);
    if (el) observer.observe(el);
    if (content.current) observer.observe(content.current);
    return () => observer.disconnect();
  });
  return {
    viewport,
    content,
    onScroll: () => {
      const el = viewport.current;
      if (el)
        following.current =
          el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    },
    follow: () => {
      following.current = true;
      bottom();
    },
    preservePrepend: () => {
      const el = viewport.current;
      if (el) {
        following.current = false;
        prepend.current = { height: el.scrollHeight, top: el.scrollTop };
      }
    },
  };
}
