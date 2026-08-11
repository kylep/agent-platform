import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the running app at /apps/running/ (nginx passes the full path
// through).
export default defineConfig({
  base: "/apps/running/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/apps/running/api": "http://localhost:8000" },
  },
});
