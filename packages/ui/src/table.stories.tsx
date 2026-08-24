import type { Meta, StoryObj } from "@storybook/react-vite";
import { StatusChip } from "./chip";
import { Table, TD, TH } from "./table";

const meta: Meta = { title: "UI/Table" };
export default meta;

// Narrow enough that the last column runs past the edge: the right side fades
// while there is content behind it, and the fade moves to the left once you
// scroll to the end.
export const Overflowing: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 320 }}>
      <Table>
        <thead><tr><TH>ID</TH><TH>Agent</TH><TH>State</TH><TH>Created</TH></tr></thead>
        <tbody>
          <tr>
            <TD><a href="#run">a1b2c3d4</a></TD><TD>health-monitor</TD>
            <TD><StatusChip status="succeeded" /></TD>
            <TD className="text-muted whitespace-nowrap">7/31/2026, 9:15:00 AM</TD>
          </tr>
          <tr>
            <TD><a href="#run">e5f6a7b8</a></TD><TD>news-librarian</TD>
            <TD><StatusChip status="running" /></TD>
            <TD className="text-muted whitespace-nowrap">7/31/2026, 9:00:00 AM</TD>
          </tr>
        </tbody>
      </Table>
    </div>
  ),
};

export const Runs: StoryObj = {
  render: () => (
    <Table style={{ maxWidth: 560 }}>
      <thead><tr><TH>ID</TH><TH>Agent</TH><TH>State</TH><TH>Created</TH></tr></thead>
      <tbody>
        <tr>
          <TD><a href="#run">a1b2c3d4</a></TD><TD>health-monitor</TD>
          <TD><StatusChip status="succeeded" /></TD>
          <TD className="text-muted">7/31/2026, 9:15:00 AM</TD>
        </tr>
        <tr>
          <TD><a href="#run">e5f6a7b8</a></TD><TD>news</TD>
          <TD><StatusChip status="rejected" /></TD>
          <TD className="text-muted">7/31/2026, 9:00:00 AM</TD>
        </tr>
      </tbody>
    </Table>
  ),
};
