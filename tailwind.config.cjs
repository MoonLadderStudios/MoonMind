/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./api_service/templates/task_dashboard.html",
    "./api_service/static/task_dashboard/**/*.js",
  ],
  darkMode: "class",
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        "mm-bg": "rgb(var(--mm-bg) / <alpha-value>)",
        "mm-panel": "rgb(var(--mm-panel) / <alpha-value>)",
        "mm-ink": "rgb(var(--mm-ink) / <alpha-value>)",
        "mm-muted": "rgb(var(--mm-muted) / <alpha-value>)",
        "mm-border": "rgb(var(--mm-border) / <alpha-value>)",
        "mm-accent": "rgb(var(--mm-accent) / <alpha-value>)",
        "mm-accent-2": "rgb(var(--mm-accent-2) / <alpha-value>)",
        "mm-accent-warm": "rgb(var(--mm-accent-warm) / <alpha-value>)",
        "mm-ok": "rgb(var(--mm-ok) / <alpha-value>)",
        "mm-warn": "rgb(var(--mm-warn) / <alpha-value>)",
        "mm-danger": "rgb(var(--mm-danger) / <alpha-value>)",
      },
      borderRadius: {
        mm: "0.9rem",
      },
      boxShadow: {
        mm: "var(--mm-shadow)",
        mmGlow: "0 0 0 1px rgb(var(--mm-accent) / 0.55), 0 10px 40px -20px rgb(var(--mm-accent) / 0.65)",
      },
      backdropBlur: {
        mm: "18px",
      },
      transitionTimingFunction: {
        mm: "cubic-bezier(.2,.8,.2,1)",
      },
    },
  },
  safelist: [],
};
