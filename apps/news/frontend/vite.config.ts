import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the news app at /apps/news/ (nginx passes the full path through).
export default defineConfig({
  base: "/apps/news/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/apps/news/api": "http://localhost:8000" },
  },
});
