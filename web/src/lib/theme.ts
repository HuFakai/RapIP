export const themes = [
  { id: "blue", name: "冰蓝", color: "#8db8ff", caption: "默认" },
  { id: "violet", name: "紫罗兰", color: "#b9a2ff", caption: "Violet" },
  { id: "amber", name: "琥珀", color: "#f4c477", caption: "Amber" },
  { id: "rose", name: "玫瑰", color: "#f3a6c4", caption: "Rose" },
] as const;
export type ThemeId = (typeof themes)[number]["id"];
export function readTheme(): ThemeId {
  try {
    const stored = localStorage.getItem("rapip.theme");
    return themes.find((theme) => theme.id === stored)?.id || "blue";
  } catch {
    return "blue";
  }
}
export function applyTheme(theme: ThemeId): void {
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", "#101216");
  try {
    localStorage.setItem("rapip.theme", theme);
  } catch {
    /* Browsing without persistence still works. */
  }
}
