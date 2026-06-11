// Minimal type stub for react-scrollama (ships without bundled types).
declare module "react-scrollama" {
  import type { ReactNode } from "react";

  export interface ScrollamaProps {
    offset?: number;
    onStepEnter?: (e: { data: number; element: HTMLElement; direction: string }) => void;
    onStepExit?: (e: { data: number; element: HTMLElement; direction: string }) => void;
    children?: ReactNode;
  }
  export function Scrollama(props: ScrollamaProps): JSX.Element;

  export interface StepProps {
    data: number;
    children?: ReactNode;
  }
  export function Step(props: StepProps): JSX.Element;
}
