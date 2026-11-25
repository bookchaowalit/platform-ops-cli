// design-floor tailwind-v3 1.0.0
// Tailwind v3 mapping for tokens.css (8 apps on v3 at 2026-10-28). Merge into
// `theme.extend` of tailwind.config.js and set darkMode as below. Upgrade the
// app to v4 when it is next touched and switch to tailwind/v4.css.
module.exports = {
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        fg: "var(--color-fg)",
        "fg-muted": "var(--color-fg-muted)",
        accent: "var(--color-accent)",
        "accent-fg": "var(--color-accent-fg)",
        border: "var(--color-border)",
        focus: "var(--color-focus)",
        danger: "var(--color-danger)",
        success: "var(--color-success)",
      },
      fontFamily: {
        sans: "var(--font-sans)",
        serif: "var(--font-serif)",
        mono: "var(--font-mono)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
      },
    },
  },
};
