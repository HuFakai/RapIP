import { useState, type CSSProperties } from "react";
import { Check, Palette } from "lucide-react";
import { applyTheme, readTheme, themes } from "@/lib/theme";

export function AppearanceSettings() {
  const [selected, setSelected] = useState(readTheme);
  function choose(index: number) {
    const theme = themes[(index + themes.length) % themes.length];
    applyTheme(theme.id);
    setSelected(theme.id);
  }
  return (
    <section className="surface appearance-surface">
      <div className="section-heading">
        <div>
          <span className="eyebrow">APPEARANCE</span>
          <h2>主题颜色</h2>
        </div>
        <span className="small muted">
          <Palette size={14} /> 自动保存在当前浏览器
        </span>
      </div>
      <div className="theme-options" role="radiogroup" aria-label="主题颜色">
        {themes.map((theme, index) => (
          <button
            key={theme.id}
            type="button"
            role="radio"
            aria-checked={selected === theme.id}
            aria-label={theme.name}
            tabIndex={selected === theme.id ? 0 : -1}
            className={`theme-option ${selected === theme.id ? "selected" : ""}`}
            onClick={() => choose(index)}
            onKeyDown={(event) => {
              if (
                ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
                  event.key,
                )
              ) {
                event.preventDefault();
                const next =
                  (index +
                    (["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1) +
                    themes.length) %
                  themes.length;
                choose(next);
                (
                  event.currentTarget.parentElement?.children[
                    next
                  ] as HTMLElement
                )?.focus();
              }
            }}
          >
            <span
              className="theme-preview"
              style={{ "--swatch": theme.color } as CSSProperties}
            >
              <i />
              <i />
              <i />
            </span>
            <span>
              <strong>{theme.name}</strong>
              <small>{theme.caption}</small>
            </span>
            {selected === theme.id && <Check size={16} />}
          </button>
        ))}
      </div>
    </section>
  );
}
