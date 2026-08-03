import type { Meta, StoryObj } from "@storybook/react-vite";
import { Stat, StatRow } from "./stat";

const meta: Meta = { title: "UI/Stat" };
export default meta;

export const Row: StoryObj = {
  render: () => (
    <StatRow>
      <Stat label="success rate" value="98%" />
      <Stat label="runs · 24h" value={136} />
      <Stat label="dlq depth" value={3} warn />
      <Stat label="avg duration" value="18.3s" to="/reporting" />
    </StatRow>
  ),
};
