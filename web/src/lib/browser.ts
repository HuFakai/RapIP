export function randomId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      /* Fall back for HTTP/IP deployments and denied clipboard permission. */
    }
  }
  const previous = document.activeElement;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.cssText =
    "position:fixed;left:0;top:0;opacity:0;pointer-events:none";
  // Keep the temporary control within an open dialog's focus trap.
  (document.querySelector('[role="dialog"]') || document.body).append(textarea);
  textarea.select();
  try {
    if (!document.execCommand("copy")) throw new Error("请手动选中并复制内容");
  } finally {
    textarea.remove();
    if (previous instanceof HTMLElement) previous.focus();
  }
}
