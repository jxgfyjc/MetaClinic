// 象元 mark: an elephant-trunk-curve inside a circle ("元" = origin) — drawn as inline SVG so it needs no asset pipeline.
export function Logo({ size = 28, light = false }: { size?: number; light?: boolean }) {
  const c = light ? "#fff" : "#2656c4";
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <rect width="32" height="32" rx="9" fill={light ? "rgba(255,255,255,.16)" : "#eef5ff"} />
      <circle cx="16" cy="16" r="9.2" stroke={c} strokeWidth="2" />
      <path d="M11.5 17.2c2.6 3.2 7.2 3 8.9-.4 1.2-2.5-.2-5.1-2.7-5.1" stroke={c} strokeWidth="2" strokeLinecap="round" />
      <circle cx="12.4" cy="13" r="1.15" fill={c} />
    </svg>
  );
}
