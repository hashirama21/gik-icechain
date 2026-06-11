import type { Config } from "tailwindcss";

// Design tokens mirror dashboard/GIK-IceChain-Dashboard-v4.html (do not edit that file).
const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Sora", "system-ui", "sans-serif"],
        mono: ["Space Mono", "monospace"],
      },
      colors: {
        // Risk traffic-light (canonical 4-class, matches titiler_config risk_levels).
        risk: {
          green: "#10B981",
          yellow: "#F59E0B",
          orange: "#FF9800",
          red: "#FF2626",
          nodata: "#445577",
        },
      },
    },
  },
  plugins: [],
};

export default config;
