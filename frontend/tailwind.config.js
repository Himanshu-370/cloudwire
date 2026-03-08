/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07090f",
        matrix: "#8eff5a",
        cyanline: "#00e7ff",
        hotpink: "#ff3fd4",
        violetwire: "#9f7dff",
        ember: "#ff8f38",
      },
      boxShadow: {
        neon: "0 0 22px rgba(0, 231, 255, 0.25)",
      },
      fontFamily: {
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};
