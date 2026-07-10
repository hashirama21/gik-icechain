// cal-heatmap ships types at src/index.d.ts but its package.json "exports"
// hides them from TS module resolution; declare the entrypoints we use.
declare module "cal-heatmap" {
  export default class CalHeatmap {
    paint(options: object, plugins?: unknown[][]): Promise<unknown>;
    previous(n?: number): Promise<unknown>;
    next(n?: number): Promise<unknown>;
    on(event: string, callback: (...args: never[]) => void): void;
    destroy(): Promise<unknown>;
  }
}

declare module "cal-heatmap/plugins/Tooltip" {
  const Tooltip: unknown;
  export default Tooltip;
}

declare module "cal-heatmap/plugins/CalendarLabel" {
  const CalendarLabel: unknown;
  export default CalendarLabel;
}

declare module "cal-heatmap/cal-heatmap.css";
