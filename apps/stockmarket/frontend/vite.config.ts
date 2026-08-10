import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the stockmarket app at /apps/stockmarket/ (nginx passes the full
// path through).
export default defineConfig({
  base: "/apps/stockmarket/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/apps/stockmarket/api": "http://localhost:8000" },
  },
});
