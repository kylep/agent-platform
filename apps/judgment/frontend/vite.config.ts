import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the judgment app at /apps/judgment/ (nginx passes the full path through).
export default defineConfig({
  base: "/apps/judgment/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/apps/judgment/api": "http://localhost:8000" },
  },
});
