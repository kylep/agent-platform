import type { Meta, StoryObj } from "@storybook/react-vite";
import { buildPlatformNav, SideNav } from "./sidenav";

const meta: Meta = { title: "UI/SideNav" };
export default meta;

// The shared platform shell sidebar (console + app frontends). Deployed apps
// slot in under the Apps accordion; the active path drives highlight and
// group auto-expand; badge counts surface pending work.

export const ConsoleOnDashboard: StoryObj = {
  render: () => (
    <div style={{ height: 480, display: "flex" }}>
      <SideNav entries={buildPlatformNav([{ name: "news", icon: "🗞️" }])}
               activePath="/" badges={{ "/changes": 2 }} />
    </div>
  ),
};

export const InsideAnApp: StoryObj = {
  render: () => (
    <div style={{ height: 480, display: "flex" }}>
      <SideNav entries={buildPlatformNav([{ name: "news", icon: "🗞️" }])}
               activePath="/apps/news/" />
    </div>
  ),
};
