import { useState } from "react";
import { ArrowRight, BookOpen, X } from "lucide-react";
import { Button } from "./ui/button";
const key = "rapip:guide:intro:v1";
export function GuideBanner() {
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(key) === "dismissed";
    } catch {
      return false;
    }
  });
  if (hidden) return null;
  return (
    <aside className="guide-banner">
      <BookOpen size={20} />
      <div>
        <strong>从第一条代理链接开始</strong>
        <span>按步骤接入，了解固定 IP、换号、质量结果与异常处理。</span>
      </div>
      <Button asChild size="sm" variant="secondary">
        <a href="#guide">
          打开新手引导 <ArrowRight size={13} />
        </a>
      </Button>
      <button
        type="button"
        className="guide-dismiss"
        aria-label="稍后再看新手引导"
        onClick={() => {
          setHidden(true);
          try {
            localStorage.setItem(key, "dismissed");
          } catch {
            /* Browser storage is optional. */
          }
        }}
      >
        <X size={16} />
      </button>
    </aside>
  );
}
