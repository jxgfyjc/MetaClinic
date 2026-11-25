import type { Config } from "tailwindcss";

// Design tokens: calm clinical blue (trust) + jade accent (TCM, "ok") + warm amber/red reserved for safety signals.
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#0b1b2b", 900: "#12263a", 700: "#33475b", 500: "#5b6f84", 400: "#8497ab", 300: "#b4c1cf", 200: "#d7dfe8", 100: "#e9eef4", 50: "#f4f7fa" },
        brand: { 50: "#eef5ff", 100: "#d9e8ff", 200: "#b5d1ff", 300: "#86b2fb", 400: "#5a8ff2", 500: "#3a6fe0", 600: "#2656c4", 700: "#1e44a0", 800: "#1b377d", 900: "#172e63" },
        jade: { 50: "#eefaf5", 100: "#d3f2e4", 200: "#a8e4cb", 400: "#3fbf8f", 500: "#1fa575", 600: "#168560", 700: "#126b4e" },
        amber: { 50: "#fff8eb", 100: "#feecc8", 400: "#f4b13c", 500: "#e29714", 600: "#b9740b", 700: "#8f5709" },
        danger: { 50: "#fff1f1", 100: "#ffdcdc", 400: "#f06a6a", 500: "#dc3c3c", 600: "#b82b2b", 700: "#8f2020" },
      },
      fontFamily: {
        sans: ['"PingFang SC"', '"Microsoft YaHei"', '"Source Han Sans SC"', '"Noto Sans CJK SC"', "Inter", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(18,38,58,.05), 0 1px 1px rgba(18,38,58,.03)",
        pop: "0 12px 32px -8px rgba(18,38,58,.22), 0 2px 6px rgba(18,38,58,.08)",
        ring: "0 0 0 3px rgba(58,111,224,.18)",
      },
      borderRadius: { xl2: "14px" },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        pop: { "0%": { opacity: "0", transform: "translateY(6px) scale(.98)" }, "100%": { opacity: "1", transform: "none" } },
        slide: { "0%": { opacity: "0", transform: "translateX(18px)" }, "100%": { opacity: "1", transform: "none" } },
        pulseDot: { "0%,100%": { opacity: ".35", transform: "scale(.85)" }, "50%": { opacity: "1", transform: "scale(1)" } },
      },
      animation: { shimmer: "shimmer 1.4s infinite", pop: "pop .16s ease-out", slide: "slide .2s ease-out", pulseDot: "pulseDot 1.1s ease-in-out infinite" },
    },
  },
  plugins: [],
} satisfies Config;
