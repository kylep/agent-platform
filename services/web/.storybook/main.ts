import type { StorybookConfig } from "@storybook/react-vite";

const config: StorybookConfig = {
  framework: "@storybook/react-vite",
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  // Ship under the site at /storybook/ (Dockerfile copies storybook-static).
  viteFinal: (cfg) => ({ ...cfg, base: "./" }),
};

export default config;
