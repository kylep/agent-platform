import type { Meta, StoryObj } from "@storybook/react-vite";
import { NavLink, useLocation } from "react-router-dom";
import { buildPlatformNav, SideNav, ThemeToggle } from "./sidenav";

const meta: Meta = { title: "UI/SideNav" };
export default meta;

// The shared platform shell sidebar (console + app frontends). Deployed apps
// slot in under the Apps accordion; the active path drives highlight and
// group auto-expand; badge counts surface pending work.

const consoleEntries = buildPlatformNav([{ name: "news", icon: "🗞️", to: "/apps/news/" }]);

function ConsoleNav() {
  const { pathname } = useLocation();
  return (
    <div className="flex min-h-[620px]">
      <SideNav entries={consoleEntries} activePath={pathname} badges={{ "/changes": 2 }}
               LinkComponent={({ className, ...props }) => (
                 <NavLink {...props} className={({ isActive }) => className(isActive)} />
               )} />
    </div>
  );
}

export const ConsoleOnDashboard: StoryObj = {
  render: () => <ConsoleNav />,
};

export const InsideAnApp: StoryObj = {
  render: () => (
    <div className="flex min-h-[620px]">
      <SideNav entries={buildPlatformNav([{ name: "news", icon: "🗞️" }])}
               activePath="/apps/news/" />
    </div>
  ),
};

export const NoApps: StoryObj = {
  render: () => (
    <div className="flex min-h-[620px]">
      <SideNav entries={buildPlatformNav()} activePath="/apps" />
    </div>
  ),
};

export const ThemeSwitch: StoryObj = {
  render: () => <ThemeToggle />,
};
