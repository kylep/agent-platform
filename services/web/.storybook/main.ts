import type { StorybookConfig } from "@storybook/react-vite";

const config: StorybookConfig = {
  framework: "@storybook/react-vite",
  // The design system's stories live with the package; web-specific stories
  // (if any) stay under src/.
  stories: ["../../../packages/ui/src/**/*.stories.@(ts|tsx)",
            "../src/**/*.stories.@(ts|tsx)"],
  // Ship under the site at /storybook/ (Dockerfile copies storybook-static).
  viteFinal: (cfg) => ({ ...cfg, base: "./" }),
};

export default config;
