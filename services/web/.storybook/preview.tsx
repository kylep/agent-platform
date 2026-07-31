import type { Preview } from "@storybook/react-vite";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import "../src/design-system/tokens.css";
import "../src/app.css";

// Render stories on the real canvas token, inside a router (primitives like
// Stat link with react-router). The toolbar exposes the dark/light swap.
const preview: Preview = {
  globalTypes: {
    theme: {
      description: "Design-system theme",
      toolbar: { title: "Theme", items: ["dark", "light"] },
    },
  },
  initialGlobals: { theme: "dark" },
  decorators: [
    (Story, ctx) => {
      document.documentElement.dataset.theme = ctx.globals.theme ?? "dark";
      return (
        <MemoryRouter>
          <div style={{ background: "var(--ds-canvas)", color: "var(--ds-text)", padding: 24, minHeight: "100vh" }}>
            <Story />
          </div>
        </MemoryRouter>
      );
    },
  ],
};

export default preview;
