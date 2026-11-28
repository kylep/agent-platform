import type { Meta, StoryObj } from "@storybook/react-vite";
import MapCanvas, { type VCharacter } from "./MapCanvas";

const meta: Meta<typeof MapCanvas> = { title: "Village/MapCanvas", component: MapCanvas };
export default meta;
type Story = StoryObj<typeof MapCanvas>;

const characters: VCharacter[] = [
  { id: "pai", name: "Pai", face: "🦉", x: 20, y: 18, now: "carrying wood to the dock",
    steps: [{ op: "walk", target: "dock" }, { op: "drop" }, { op: "pickup", target: "reeds" }] },
  { id: "kai", name: "Kai", face: "🦊", x: 36, y: 22, blocked_reason: "no rope" },
  { id: "olu", name: "Olu", face: "🐢", x: 30, y: 10, thinking: true },
];
const objects = [{ id: "o1", x: 24, y: 20 }, { id: "o2", x: 40, y: 14 }];

export const WholeVillage: Story = { args: { characters, objects, whole: true } };
export const FollowingOne: Story = { args: { characters, objects, whole: false, follow: "pai" } };
export const Empty: Story = { args: { characters: [], objects: [] } };
